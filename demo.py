"""Build an isolated local search index from synthetic demonstration fixtures."""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parent
DEFAULT_DEMO_ROOT = ROOT / "data" / "demo"
FIXTURES = ROOT / "examples" / "demo" / "fixtures.json"
MARKER = ".github-star-index-demo.json"
DEMO_NOTICE = "合成演示数据：以下仓库、源码、功能和证据均为虚构示例，不代表真实 GitHub 项目。"
FIXTURE_TIME = "2026-01-01T00:00:00+00:00"


def atomic_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temp.replace(path)


def safe_name(full_name: str) -> tuple[str, str]:
    parts = full_name.split("/") if isinstance(full_name, str) else []
    if len(parts) != 2 or any(part in {"", ".", ".."} for part in parts):
        raise ValueError(f"Unsafe synthetic repository name: {full_name!r}")
    if any(not all(char.isascii() and (char.isalnum() or char in "_.-") for char in part) for part in parts):
        raise ValueError(f"Unsupported synthetic repository name: {full_name!r}")
    return parts[0], "__".join(parts)


def safe_fixture_path(value: str) -> PurePosixPath:
    if not isinstance(value, str) or not value or "\\" in value or "\x00" in value or ":" in value:
        raise ValueError(f"Unsafe fixture path: {value!r}")
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in value.split("/")):
        raise ValueError(f"Unsafe fixture path: {value!r}")
    return path


def require_owned_demo_root(root: Path) -> None:
    marker_path = root / MARKER
    catalog_path = root / "data" / "catalog.json"
    if marker_path.is_file():
        try:
            marker = json.loads(marker_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise ValueError(f"Refusing to use invalid demo marker: {marker_path}") from exc
        if marker.get("kind") != "synthetic-demo" or marker.get("user") != "demo":
            raise ValueError(f"Refusing to overwrite an output root without our synthetic-demo marker: {root}")
        if catalog_path.is_file():
            catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
            if catalog.get("user") != "demo":
                raise ValueError(f"Refusing to overwrite catalog for {catalog.get('user')!r}: {catalog_path}")
        return
    if catalog_path.exists():
        try:
            catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
            owner = catalog.get("user")
        except (OSError, UnicodeError, json.JSONDecodeError):
            owner = "unknown"
        raise ValueError(f"Refusing to overwrite an unmarked catalog (user={owner!r}): {catalog_path}")
    if root.exists() and any(root.iterdir()):
        raise ValueError(f"Refusing to write into a non-empty directory without a demo marker: {root}")


def deterministic_sha(full_name: str, files: dict[str, str]) -> str:
    payload = json.dumps({"full_name": full_name, "files": files}, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()


def prepare_repository(root: Path, fixture: dict, evidence_url) -> dict:
    full_name = fixture["full_name"]
    _, disk_slug = safe_name(full_name)
    files = fixture["files"]
    if not isinstance(files, dict) or not files:
        raise ValueError(f"Fixture {full_name} has no source files")
    source_sha = deterministic_sha(full_name, files)
    source_base = root / "data" / "sources" / disk_slug
    snapshot = source_base / "snapshots" / source_sha
    manifest_files = []
    tree_entries = []
    for remote_path, content in sorted(files.items()):
        relative = safe_fixture_path(remote_path)
        if not isinstance(content, str):
            raise ValueError(f"Fixture {full_name}:{remote_path} content must be text")
        target = snapshot / "files" / Path(*relative.parts)
        resolved_root = root.resolve()
        target.resolve().relative_to(resolved_root)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8", newline="")
        encoded = content.encode("utf-8")
        git_blob_sha = hashlib.sha1(b"blob " + str(len(encoded)).encode("ascii") + b"\0" + encoded).hexdigest()
        manifest_files.append({
            "path": remote_path,
            "local_path": target.relative_to(resolved_root).as_posix(),
            "status": "ok",
            "source_sha": source_sha,
            "sha256": hashlib.sha256(encoded).hexdigest(),
            "bytes": len(encoded),
            "lines": len(content.splitlines()),
            "url": None,
        })
        tree_entries.append({"path": remote_path, "type": "blob", "sha": git_blob_sha, "size": len(encoded)})
    tree_data = {"sha": source_sha, "truncated": False, "tree": tree_entries}
    atomic_json(snapshot / "tree.json", tree_data)
    atomic_json(source_base / "tree.json", tree_data)
    manifest = {
        "full_name": full_name,
        "repository_url": None,
        "default_branch": "synthetic-demo",
        "source_sha": source_sha,
        "fetched_at": FIXTURE_TIME,
        "updated_at": FIXTURE_TIME,
        "files": manifest_files,
        "errors": [],
        "readme_path": "README.md" if "README.md" in files else None,
        "tree_path": (snapshot / "tree.json").relative_to(root).as_posix(),
        "tree_truncated": False,
        "tree_entries": len(tree_entries),
        "capture_complete": True,
        "synthetic_demo": True,
    }
    atomic_json(source_base / "manifest.json", manifest)
    atomic_json(snapshot / "manifest.json", manifest)

    evidence_by_id: dict[str, dict] = {}
    evidence_records = []
    for raw in fixture["evidence"]:
        path = raw["path"]
        safe_fixture_path(path)
        if path not in files:
            raise ValueError(f"Fixture evidence references a missing file: {full_name}:{path}")
        lines = files[path].splitlines()
        start = raw["start_line"]
        end = raw.get("end_line", start)
        if not isinstance(start, int) or not isinstance(end, int) or start < 1 or end < start or end > len(lines) or end - start > 11:
            raise ValueError(f"Fixture evidence has an invalid line range: {full_name}:{path}:{start}-{end}")
        evidence = {
            "id": raw["id"],
            "path": path,
            "start_line": start,
            "end_line": end,
            "excerpt": "\n".join(lines[start - 1:end]),
            "claim": f"合成演示：{raw['claim']}",
            "url": evidence_url(full_name, source_sha, path, start, end),
            "local_path": next(item["local_path"] for item in manifest_files if item["path"] == path),
        }
        if evidence["id"] in evidence_by_id:
            raise ValueError(f"Fixture has duplicate evidence ID: {full_name}:{evidence['id']}")
        evidence_by_id[evidence["id"]] = evidence
        evidence_records.append(evidence)

    features = []
    for raw in fixture["features"]:
        references = raw["evidence"]
        if not references or any(reference not in evidence_by_id for reference in references):
            raise ValueError(f"Fixture feature has missing evidence: {full_name}:{raw.get('name')}")
        features.append({
            **raw,
            "name": f"合成演示：{raw['name']}",
            "description": raw["description"],
        })
    card = {
        "schema_version": 1,
        "full_name": full_name,
        "kind": "library" if fixture["language"] in {"Python", "TypeScript"} else "other",
        "summary_zh": fixture["summary_zh"],
        "use_cases": fixture["use_cases"],
        "tags": fixture["tags"],
        "features": features,
        "interfaces": fixture["interfaces"],
        "requirements": fixture["requirements"],
        "limitations": fixture["limitations"],
        "comparisons": fixture["comparisons"],
        "evidence_level": "source",
        "runtime_verified": False,
        "source_sha": source_sha,
        "analyzed_at": FIXTURE_TIME,
        "evidence": evidence_records,
        "unverified": fixture["unverified"],
        "analysis_version": 1,
        "analysis_scope": sorted({item["path"] for item in evidence_records}),
        "synthetic_demo": True,
    }
    atomic_json(root / "data" / "cards" / f"{disk_slug}.json", card)
    record = {
        "id": int(hashlib.sha256(full_name.casefold().encode("utf-8")).hexdigest()[:12], 16),
        "full_name": full_name,
        "url": None,
        "description": fixture["description"],
        "starred_at": FIXTURE_TIME,
        "language": fixture["language"],
        "topics": fixture["topics"],
        "default_branch": "synthetic-demo",
        "pushed_at": FIXTURE_TIME,
        "updated_at": FIXTURE_TIME,
        "archived": False,
        "disabled": False,
        "private": False,
        "fork": False,
        "size_kb": 1,
        "license": None,
        "stars": 0,
        "homepage": None,
        "visibility": "synthetic-demo",
        "active_star": True,
        "synthetic_demo": True,
    }
    return record


def build(root: Path) -> dict:
    root = root.resolve()
    require_owned_demo_root(root)
    fixtures_text = FIXTURES.read_text(encoding="utf-8")
    fixtures = json.loads(fixtures_text)
    if not isinstance(fixtures.get("repositories"), list) or len(fixtures["repositories"]) != 4:
        raise ValueError("The synthetic demonstration corpus must define exactly four repositories")
    if not fixtures.get("notice", "").startswith("Every repository"):
        raise ValueError("Fixture source must clearly identify itself as synthetic demo content")
    root.mkdir(parents=True, exist_ok=True)
    marker = {
        "format_version": 1,
        "kind": "synthetic-demo",
        "user": "demo",
        "fixture_sha256": hashlib.sha256(fixtures_text.encode("utf-8")).hexdigest(),
        "notice": DEMO_NOTICE,
    }
    atomic_json(root / MARKER, marker)

    schema_path = ROOT / "schema.json"
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    atomic_json(root / "schema.json", schema)

    sys.path.insert(0, str(ROOT))
    from search import build_index, evidence_url

    records = []
    seen_slugs: set[str] = set()
    for fixture in fixtures["repositories"]:
        _, disk_slug = safe_name(fixture["full_name"])
        key = disk_slug.casefold()
        if key in seen_slugs:
            raise ValueError(f"Synthetic fixture repository slug collision: {disk_slug}")
        seen_slugs.add(key)
        records.append(prepare_repository(root, fixture, evidence_url))

    catalog = {
        "schema_version": 1,
        "user": "demo",
        "synced_at": FIXTURE_TIME,
        "expected_count_before": len(records),
        "expected_count_after": len(records),
        "count": len(records),
        "complete": True,
        "pages": [{"page": 1, "count": len(records)}],
        "source": "Synthetic local fixture data only; no GitHub request was made.",
        "notice": DEMO_NOTICE,
        "repositories": records,
    }
    atomic_json(root / "data" / "catalog.json", catalog)
    result = build_index(root=root)
    result["synthetic_demo"] = True
    result["notice"] = DEMO_NOTICE
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Create a separate index from clearly synthetic demo repositories.")
    parser.add_argument("--root", type=Path, default=DEFAULT_DEMO_ROOT, help="demo-only output root (default: data/demo)")
    args = parser.parse_args()
    try:
        result = build(args.root)
    except (OSError, ValueError, RuntimeError, json.JSONDecodeError) as exc:
        parser.error(str(exc))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
