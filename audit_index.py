"""Check completeness and exact correspondence of local sources and card evidence."""
from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from card_tools import finalize, safe_local
from search import _card_ready

ROOT = Path(__file__).resolve().parent


def audit(allow_pending=False, *, root=ROOT):
    root = Path(root).resolve()
    catalog = json.loads((root / "data" / "catalog.json").read_text(encoding="utf-8"))
    repos = catalog["repositories"]
    errors, warnings = [], []
    if not catalog.get("complete") or len(repos) != catalog["count"] or len({r["full_name"] for r in repos}) != len(repos):
        errors.append({"stage": "catalog", "error": "Incomplete or duplicate catalog"})
    levels = Counter()
    files = evidence = cards = 0
    pending = []
    for repo in repos:
        name = repo["full_name"]
        slug = name.replace("/", "__")
        manifest_path = root / "data" / "sources" / slug / "manifest.json"
        if not manifest_path.exists():
            errors.append({"repo": name, "stage": "manifest", "error": "Missing capture"})
            continue
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            if manifest["full_name"] != name:
                raise ValueError("Repository identity mismatch")
            tree = json.loads(safe_local(manifest["tree_path"], root=root).read_text(encoding="utf-8"))
            if tree["sha"] != manifest["source_sha"]:
                raise ValueError("Tree commit mismatch")
            if manifest.get("tree_truncated"):
                warnings.append({"repo": name, "warning": "GitHub truncated the recursive tree"})
            for item in manifest["files"]:
                if item.get("status") != "ok" or item.get("source_sha") != manifest["source_sha"]:
                    raise ValueError("Invalid captured file state")
                content = safe_local(item["local_path"], root=root).read_bytes()
                if hashlib.sha256(content).hexdigest() != item["sha256"]:
                    raise ValueError(f"Hash mismatch {item['path']}")
                files += 1
        except Exception as exc:
            errors.append({"repo": name, "stage": "source", "error": str(exc)})
        card_path = root / "data" / "cards" / f"{slug}.json"
        if not card_path.exists():
            pending.append(name)
            continue
        try:
            original = json.loads(card_path.read_text(encoding="utf-8"))
            if not _card_ready(root, name, original, manifest, manifest.get("source_sha")):
                raise ValueError("Card is not finalized for the current captured commit")
            card = finalize(json.loads(json.dumps(original)), root=root)
            for before, after in zip(original["evidence"], card["evidence"]):
                if before.get("excerpt") != after["excerpt"] or before.get("url") != after["url"]:
                    raise ValueError(f"Unfinalized or mismatched excerpt/URL {after['id']}")
            cards += 1
            evidence += len(card["evidence"])
            levels[card["evidence_level"]] += 1
        except Exception as exc:
            errors.append({"repo": name, "stage": "card", "error": str(exc)})
    if pending and not allow_pending:
        errors.append({"stage": "coverage", "error": f"{len(pending)} cards still pending"})
    return {"checked_at": datetime.now(timezone.utc).isoformat(), "ok": not errors, "catalog_count": len(repos), "valid_cards": cards, "captured_files": files, "evidence_spans": evidence, "evidence_levels": dict(levels), "pending_cards": pending, "warnings": warnings, "errors": errors}


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--allow-pending", action="store_true")
    p.add_argument("--root", type=Path, default=ROOT)
    args = p.parse_args()
    result = audit(args.allow_pending, root=args.root)
    dest = args.root.resolve() / "data" / "validation"
    dest.mkdir(exist_ok=True)
    (dest / "audit.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in result.items() if k not in ("pending_cards", "warnings")}, ensure_ascii=False, indent=2))
    raise SystemExit(not result["ok"])


if __name__ == "__main__":
    main()
