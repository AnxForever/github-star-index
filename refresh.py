"""Incrementally refresh one local GitHub Star index without running repo code."""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def run(command: list[str]) -> None:
    result = subprocess.run(command, cwd=ROOT)
    if result.returncode:
        raise SystemExit(result.returncode)


def main() -> int:
    parser = argparse.ArgumentParser(description="Refresh catalog metadata and pinned source snapshots.")
    parser.add_argument("--user", help="expected authenticated GitHub login (defaults to the current gh account)")
    parser.add_argument("--root", type=Path, default=ROOT, help="index directory (default: this project directory)")
    args = parser.parse_args()
    root = args.root.resolve()
    if (root / ".github-star-index-demo.json").exists():
        parser.error("This directory is marked as synthetic demo data and cannot be refreshed from GitHub")
    if root != ROOT.resolve() and not (root / "schema.json").is_file():
        parser.error(f"Custom index root must contain schema.json: {root}")

    catalog_command = [sys.executable, str(ROOT / "catalog_sync.py"), "--root", str(root)]
    if args.user:
        catalog_command.extend(["--user", args.user])
    run(catalog_command)
    run([sys.executable, str(ROOT / "source_sync.py"), "--all", "--refresh", "--root", str(root)])

    run([sys.executable, str(ROOT / "search.py"), "build", "--root", str(root)])
    run([sys.executable, str(ROOT / "render_catalog.py"), "--root", str(root)])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
