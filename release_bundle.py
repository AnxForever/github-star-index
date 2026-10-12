"""Package only explicitly listed source files, excluding all personal data."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

ROOT = Path(__file__).resolve().parent
VERSION = "0.1.0-preview"
PUBLIC_FILES = (
    ".gitignore", "ANALYSIS_GUIDE.md", "LICENSE", "README.md", "CONTRIBUTING.md", "CHANGELOG.md",
    "schema.json", "search.py", "embeddings.py", "catalog_sync.py", "source_sync.py", "refresh.py", "card_tools.py",
    "render_catalog.py", "audit_index.py", "analysis.py", "service.py", "agent_context.py", "webapp.py", "mcp_server.py",
    "demo.py", "release_bundle.py", "eval_retrieval.py", "examples/demo/fixtures.json",
    "tests/test_search.py", "tests/test_sources.py", "tests/test_embeddings.py",
    "web/index.html", "web/app.js", "web/styles.css",
    "web/icons.js", "web/theme.js", "web/THIRD_PARTY_NOTICES.txt",
    "web/fonts/Geist-Variable.woff2", "web/fonts/OFL.txt",
    "docs/README.zh-CN.md", "docs/PLAN.md", "docs/COMPETITORS.md", "docs/BUSINESS_AND_ADOPTION.md",
    "docs/INTERFACES.md", "docs/UI_DESIGN.md", "docs/IMPLEMENTATION_STATUS.md",
    "docs/AI_ONBOARDING.md", "docs/REQUIREMENTS.md", "docs/HANDOFF.md", "skills/repository-reuse/SKILL.md",
    ".github/ISSUE_TEMPLATE/retrieval.md", ".github/ISSUE_TEMPLATE/feature_request.md",
)


def package(output: Path) -> dict:
    payloads: list[tuple[str, bytes]] = []
    for name in PUBLIC_FILES:
        path = ROOT / name
        resolved = path.resolve(strict=True)
        resolved.relative_to(ROOT)
        if path.is_symlink() or not resolved.is_file():
            raise ValueError("Public source must be a regular file: " + name)
        payloads.append((name, resolved.read_bytes()))
    manifest = {
        "version": VERSION, "contains_personal_data": False,
        "scope": "Explicit source allowlist only; synthetic fixtures included, generated data excluded.",
        "files": [{"path": name, "sha256": hashlib.sha256(content).hexdigest(), "bytes": len(content)}
                  for name, content in payloads],
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    # Exclusive creation preserves any previous bundle or user-selected file.
    with ZipFile(output, "x", compression=ZIP_DEFLATED) as archive:
        prefix = "github-star-index/"
        for name, content in payloads:
            archive.writestr(prefix + name, content)
        archive.writestr(prefix + "PUBLIC_BUNDLE.json", json.dumps(manifest, ensure_ascii=False, indent=2))
    digest = hashlib.sha256(output.read_bytes()).hexdigest()
    return {"path": str(output.resolve()), "version": VERSION, "source_files": len(payloads),
            "bytes": output.stat().st_size, "sha256": digest, "scope": manifest["scope"]}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "dist" / f"github-star-index-{VERSION}.zip")
    args = parser.parse_args()
    try:
        result = package(args.output)
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
