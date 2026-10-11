"""Capture pinned repository evidence without cloning or running repository code."""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import re
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from urllib.parse import quote

ROOT = Path(__file__).resolve().parent
SOURCES = ROOT / "data" / "sources"


def now():
    return datetime.now(timezone.utc).isoformat()


def slug(name: str):
    parts = name.split("/") if isinstance(name, str) else []
    if (len(parts) != 2 or any(part in {"", ".", ".."} or not re.fullmatch(r"[A-Za-z0-9_.-]+", part) for part in parts)):
        raise ValueError("Expected a safe owner/repo identity without dot segments")
    return "__".join(parts)


def validate_repo_slugs(repos: list[dict]):
    seen_names: set[str] = set()
    seen_slugs: dict[str, str] = {}
    for repo in repos:
        name = repo.get("full_name")
        disk_slug = slug(name)
        name_key = name.casefold()
        if name_key in seen_names:
            raise ValueError(f"Duplicate repository identity: {name}")
        seen_names.add(name_key)
        slug_key = disk_slug.casefold()
        previous = seen_slugs.get(slug_key)
        if previous and previous.casefold() != name_key:
            raise ValueError(f"Repository slug collision between {previous!r} and {name!r}")
        seen_slugs[slug_key] = name


def atomic_json(path: Path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + f".{time.time_ns()}.tmp")
    temp.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(path)


def api(endpoint: str):
    for attempt in range(3):
        p = subprocess.run(["gh", "api", endpoint, "-H", "Accept: application/vnd.github+json"], capture_output=True, encoding="utf-8", timeout=90)
        if p.returncode == 0:
            return json.loads(p.stdout)
        err = p.stderr.strip()
        if any(t in err.lower() for t in ("rate limit", "http 429", "http 502", "http 503", "connection", "timeout")) and attempt < 2:
            time.sleep(2 ** (attempt + 1))
            continue
        raise RuntimeError(err[:1200])
    raise RuntimeError("GitHub read failed")


def safe_repo_path(path: str):
    if not isinstance(path, str) or not path or "\x00" in path:
        raise ValueError("Unsafe evidence path")
    p = PurePosixPath(path)
    if (p.is_absolute() or "\\" in path or ":" in path or
            any(part in {"", ".", ".."} for part in path.split("/")) or not p.parts):
        raise ValueError("Unsafe evidence path")
    return p


def store_file(repo: str, sha: str, obj, snapshot: Path, *, root: Path = ROOT):
    path = obj["path"]
    safe_repo_path(path)
    if obj.get("type") != "file":
        raise ValueError(f"{path}: not a regular file")
    if obj.get("size", 0) > 2_000_000:
        raise ValueError(f"{path}: text exceeds 2 MB capture bound")
    if obj.get("encoding") != "base64":
        raise ValueError(f"{path}: API did not return inline text")
    content = base64.b64decode(obj["content"], validate=False)
    text = content.decode("utf-8-sig")
    if "\0" in text:
        raise ValueError(f"{path}: binary content")
    dest = snapshot / "files" / path
    resolved_root = root.resolve()
    try:
        dest.resolve().relative_to(resolved_root)
    except (OSError, ValueError) as exc:
        raise ValueError("Evidence destination escapes the selected index root") from exc
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(text, encoding="utf-8", newline="")
    return {"path": path, "local_path": dest.relative_to(resolved_root).as_posix(), "status": "ok", "source_sha": sha,
            "sha256": hashlib.sha256(dest.read_bytes()).hexdigest(), "bytes": dest.stat().st_size,
            "lines": len(text.splitlines()), "url": f"https://github.com/{repo}/blob/{sha}/{quote(path)}"}


def capture(repo: dict, paths: list[str] | None = None, refresh=False, *, root: Path = ROOT):
    root = Path(root).resolve()
    name = repo["full_name"]
    folder = root / "data" / "sources" / slug(name)
    manifest_path = folder / "manifest.json"
    old = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else None
    if old and not refresh and not paths and old.get("capture_complete"):
        return {"repo": name, "status": "cached", "files": len(old.get("files", []))}
    if old and not refresh:
        sha = old["source_sha"]
    else:
        ref = api(f"repos/{name}/git/ref/heads/{quote(repo['default_branch'], safe='/')}")
        sha = ref["object"]["sha"]
    if not re.fullmatch(r"[0-9a-f]{40}", sha):
        raise RuntimeError("Invalid source commit")
    if refresh and old and old.get("source_sha") == sha and old.get("capture_complete") and not paths:
        old["checked_at"] = now()
        atomic_json(manifest_path, old)
        return {"repo": name, "status": "unchanged", "files": len(old.get("files", [])), "source_sha": sha}
    snapshot = folder / "snapshots" / sha
    snapshot.mkdir(parents=True, exist_ok=True)
    matching = old if old and old.get("source_sha") == sha else None
    manifest = matching or {
        "full_name": name, "repository_url": repo["url"], "default_branch": repo["default_branch"],
        "source_sha": sha, "fetched_at": now(), "files": [], "errors": [], "readme_path": None,
        "tree_truncated": None, "capture_complete": False
    }
    # Only a matching commit's file list is ever carried forward.
    files = {f["path"]: f for f in manifest.get("files", [])}
    errors = list(manifest.get("errors", []))
    if not paths:
        tree = api(f"repos/{name}/git/trees/{sha}?recursive=1")
        tree_data = {"sha": sha, "truncated": tree.get("truncated", False), "tree": [
            {k: t[k] for k in ("path", "type", "sha", "size") if k in t} for t in tree.get("tree", [])
        ]}
        atomic_json(snapshot / "tree.json", tree_data)
        atomic_json(folder / "tree.json", tree_data)
        manifest["tree_truncated"] = tree_data["truncated"]
        manifest["tree_path"] = (snapshot / "tree.json").relative_to(root).as_posix()
        manifest["tree_entries"] = len(tree_data["tree"])
        try:
            readme = api(f"repos/{name}/readme?ref={sha}")
            item = store_file(name, sha, readme, snapshot, root=root)
            files[item["path"]] = item
            manifest["readme_path"] = item["path"]
        except Exception as exc:
            errors.append({"stage": "readme", "error": str(exc), "at": now()})
        manifest["capture_complete"] = True
    else:
        for path in paths:
            safe_repo_path(path)
            if path in files and (root / files[path]["local_path"]).exists():
                continue
            try:
                obj = api(f"repos/{name}/contents/{quote(path, safe='/')}?ref={sha}")
                item = store_file(name, sha, obj, snapshot, root=root)
                files[item["path"]] = item
            except Exception as exc:
                errors.append({"stage": "file", "path": path, "error": str(exc), "at": now()})
    manifest["files"] = sorted(files.values(), key=lambda f: f["path"])
    manifest["errors"] = errors
    manifest["updated_at"] = now()
    atomic_json(manifest_path, manifest)
    atomic_json(snapshot / "manifest.json", manifest)
    return {"repo": name, "status": "captured", "files": len(files), "errors": len(errors), "source_sha": sha}


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--repo", action="append")
    p.add_argument("--all", action="store_true")
    p.add_argument("--paths", nargs="+")
    p.add_argument("--refresh", action="store_true")
    p.add_argument("--workers", type=int, default=6)
    p.add_argument("--root", type=Path, default=ROOT, help="index directory (default: this project directory)")
    args = p.parse_args()
    root = args.root.resolve()
    if (root / ".github-star-index-demo.json").exists():
        raise SystemExit("Refusing GitHub source sync into a directory marked as synthetic demo data")
    catalog = json.loads((root / "data" / "catalog.json").read_text(encoding="utf-8"))
    if not catalog.get("complete"):
        raise SystemExit("Refusing to capture from incomplete catalog")
    repos = catalog["repositories"]
    try:
        validate_repo_slugs(repos)
    except (AttributeError, TypeError, ValueError) as exc:
        raise SystemExit(f"Refusing to capture from unsafe or ambiguous catalog: {exc}") from exc
    if args.repo:
        wanted = set(args.repo)
        repos = [r for r in repos if r["full_name"] in wanted]
        if len(repos) != len(wanted):
            raise SystemExit("Repository is not in the star catalog")
    elif not args.all:
        p.error("Specify --repo owner/repo or --all")
    failures = []
    with ThreadPoolExecutor(max_workers=min(8, max(1, args.workers))) as pool:
        tasks = {pool.submit(capture, repo, args.paths, args.refresh, root=root): repo for repo in repos}
        for i, future in enumerate(as_completed(tasks), 1):
            repo = tasks[future]["full_name"]
            try:
                result = future.result()
            except Exception as exc:
                result = {"repo": repo, "status": "error", "error": str(exc)[:1200]}
                failures.append(result)
            print(json.dumps({"progress": f"{i}/{len(repos)}", **result}, ensure_ascii=False), flush=True)
    if failures:
        atomic_json(root / "data" / "capture_failures.json", {"at": now(), "failures": failures})
        raise SystemExit(1)


if __name__ == "__main__":
    main()
