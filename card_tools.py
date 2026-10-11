"""Validate analysis cards and attach literal evidence from pinned local snapshots."""
from __future__ import annotations

import argparse
import json
import re
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from urllib.parse import quote

ROOT = Path(__file__).resolve().parent
KINDS = {"app", "library", "framework", "cli", "skill", "collection", "tutorial", "research", "infrastructure", "other"}
LEVELS = {"metadata", "readme", "docs", "source"}


def safe_local(value, root=ROOT):
    root = Path(root).resolve()
    path = (root / value).resolve()
    path.relative_to(root)
    return path


def repo_slug(name: str) -> str:
    parts = name.split("/") if isinstance(name, str) else []
    if (len(parts) != 2 or any(part in {"", ".", ".."} or not re.fullmatch(r"[A-Za-z0-9_.-]+", part) for part in parts)):
        raise ValueError("Invalid or unsafe repository identity")
    return "__".join(parts)


def validate_catalog_slugs(root=ROOT):
    catalog_path = Path(root).resolve() / "data" / "catalog.json"
    if not catalog_path.is_file():
        return
    catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
    seen_names: set[str] = set()
    seen_slugs: dict[str, str] = {}
    for repo in catalog.get("repositories", []):
        name = repo.get("full_name")
        disk_slug = repo_slug(name)
        name_key = name.casefold()
        if name_key in seen_names:
            raise ValueError(f"Duplicate repository identity: {name}")
        seen_names.add(name_key)
        slug_key = disk_slug.casefold()
        previous = seen_slugs.get(slug_key)
        if previous and previous.casefold() != name_key:
            raise ValueError(f"Repository slug collision between {previous!r} and {name!r}")
        seen_slugs[slug_key] = name


def finalize(card: dict, root=ROOT):
    root = Path(root).resolve()
    name = card["full_name"]
    disk_slug = repo_slug(name)
    manifest = json.loads((root / "data" / "sources" / disk_slug / "manifest.json").read_text(encoding="utf-8"))
    if manifest.get("full_name") != name:
        raise ValueError("Source manifest repository identity differs from the card")
    if card.get("source_sha") != manifest["source_sha"]:
        raise ValueError("Card commit differs from captured evidence")
    if card.get("kind") not in KINDS or card.get("evidence_level") not in LEVELS:
        raise ValueError("Invalid kind/evidence_level")
    if card.get("runtime_verified") is not False:
        raise ValueError("This indexing pass did not run repository code; runtime_verified must be false")
    for key in ("summary_zh", "use_cases", "tags", "features", "interfaces", "requirements", "limitations", "unverified"):
        if key not in card:
            raise ValueError(f"Missing {key}")
    files = {f["path"]: f for f in manifest["files"] if f.get("status") == "ok"}
    ids = set()
    for evidence in card.get("evidence", []):
        if evidence["id"] in ids:
            raise ValueError("Duplicate evidence id")
        ids.add(evidence["id"])
        path = evidence["path"]
        pure = PurePosixPath(path)
        if pure.is_absolute() or ".." in pure.parts or "\\" in path:
            raise ValueError("Unsafe evidence path")
        item = files.get(path)
        if not item:
            raise ValueError(f"Evidence {path} was not captured at this commit")
        lines = safe_local(item["local_path"], root=root).read_text(encoding="utf-8").splitlines()
        start, end = evidence["start_line"], evidence.get("end_line", evidence["start_line"])
        if not isinstance(start, int) or not isinstance(end, int) or not 1 <= start <= end <= len(lines):
            raise ValueError(f"Invalid line range {path}:{start}-{end}, length={len(lines)}")
        if end - start > 11:
            raise ValueError("Use focused evidence spans of at most 12 lines")
        evidence["end_line"] = end
        evidence["excerpt"] = "\n".join(lines[start - 1:end])
        evidence["url"] = f"https://github.com/{name}/blob/{card['source_sha']}/{quote(path)}#L{start}-L{end}"
        evidence["local_path"] = item["local_path"]
    for feature in card["features"]:
        if not feature.get("evidence") or not set(feature["evidence"]).issubset(ids):
            raise ValueError(f"Missing valid evidence for feature {feature.get('name')}")
        if feature.get("evidence_level") not in LEVELS:
            raise ValueError("Every feature needs an explicit evidence_level")
        cited = [e for e in card.get("evidence", []) if e["id"] in feature["evidence"]]
        doc_extensions = {".md", ".mdx", ".rst", ".txt", ".pdf"}
        if feature["evidence_level"] == "source" and all(PurePosixPath(e["path"]).suffix.lower() in doc_extensions for e in cited):
            raise ValueError(f"Feature {feature.get('name')}: source level cites only documents; add implementation evidence or use readme/docs")
    if card.get("evidence_level") != "metadata" and not ids:
        raise ValueError("Non-metadata card must cite captured evidence")
    card["schema_version"] = 1
    card["analysis_version"] = 1
    card.setdefault("analyzed_at", datetime.now(timezone.utc).isoformat())
    card["analysis_scope"] = sorted({e["path"] for e in card.get("evidence", [])})
    return card


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("paths", nargs="+")
    parser.add_argument("--root", type=Path, default=ROOT, help="index directory (default: this project directory)")
    args = parser.parse_args()
    root = args.root.resolve()
    failed = 0
    try:
        validate_catalog_slugs(root)
    except Exception as exc:
        parser.error(f"Unsafe or ambiguous catalog: {exc}")
    for raw in args.paths:
        p = Path(raw)
        if not p.is_absolute() and args.root != ROOT:
            p = root / p
        try:
            card = finalize(json.loads(p.read_text(encoding="utf-8")), root=root)
            temp = p.with_suffix(".tmp")
            temp.write_text(json.dumps(card, ensure_ascii=False, indent=2), encoding="utf-8")
            temp.replace(p)
            print(json.dumps({"repo": card["full_name"], "valid": True, "evidence": len(card["evidence"])}, ensure_ascii=False))
        except Exception as exc:
            failed += 1
            print(json.dumps({"path": str(p), "valid": False, "error": str(exc)}, ensure_ascii=False))
    raise SystemExit(bool(failed))


if __name__ == "__main__":
    main()
