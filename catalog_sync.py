"""Read-only GitHub star catalog sync through an existing gh login."""
from __future__ import annotations

import argparse
import json
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
_REPO_PART = re.compile(r"[A-Za-z0-9_.-]+\Z")


def repo_slug(full_name: str) -> str:
    """Return the legacy on-disk slug after validating both path components."""
    parts = full_name.split("/") if isinstance(full_name, str) else []
    if (len(parts) != 2 or any(part in {"", ".", ".."} or not _REPO_PART.fullmatch(part) for part in parts)):
        raise ValueError(f"Unsafe repository identity: {full_name!r}")
    return "__".join(parts)


def gh_json(args: list[str]):
    p = subprocess.run(["gh", "api", *args], capture_output=True, encoding="utf-8")
    if p.returncode:
        raise RuntimeError(p.stderr.strip())
    return json.loads(p.stdout)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--user", help="expected authenticated GitHub login (defaults to the current gh account)")
    parser.add_argument("--root", type=Path, default=ROOT, help="index directory (default: this project directory)")
    opts = parser.parse_args()
    root = opts.root.resolve()
    if (root / ".github-star-index-demo.json").exists():
        raise RuntimeError("Refusing GitHub sync into a directory marked as synthetic demo data")
    data = root / "data"
    raw_dir = data / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    login = gh_json(["user"])["login"]
    if not isinstance(login, str) or not login.strip():
        raise RuntimeError("gh api user returned no authenticated login; no catalog changed")
    if opts.user and login.casefold() != opts.user.casefold():
        raise RuntimeError(f"gh login is {login}, requested {opts.user}; no catalog changed")
    user = login
    existing_catalog = data / "catalog.json"
    if existing_catalog.is_file():
        existing_user = json.loads(existing_catalog.read_text(encoding="utf-8")).get("user")
        if existing_user and str(existing_user).casefold() != user.casefold():
            raise RuntimeError("This index belongs to another GitHub account; use a separate --root. Existing catalog preserved.")
    query = 'query { viewer { login starredRepositories { totalCount } } }'
    before = gh_json(["graphql", "-f", f"query={query}"])["data"]["viewer"]["starredRepositories"]["totalCount"]
    pages = []
    entries = []
    page = 1
    while True:
        items = gh_json([f"user/starred?per_page=100&page={page}&sort=created&direction=desc", "-H", "Accept: application/vnd.github.star+json"])
        pages.append({"page": page, "count": len(items)})
        (raw_dir / f"stars-page-{page:03}.json").write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")
        entries.extend(items)
        print(json.dumps({"page": page, "count": len(items)}, ensure_ascii=False), flush=True)
        if len(items) < 100:
            break
        page += 1
    after = gh_json(["graphql", "-f", f"query={query}"])["data"]["viewer"]["starredRepositories"]["totalCount"]
    records = []
    for item in entries:
        r = item["repo"]
        records.append({
            "id": r["id"], "full_name": r["full_name"], "url": r["html_url"],
            "description": r.get("description"), "starred_at": item.get("starred_at"),
            "language": r.get("language"), "topics": r.get("topics", []),
            "default_branch": r["default_branch"], "pushed_at": r.get("pushed_at"),
            "updated_at": r.get("updated_at"), "archived": r.get("archived", False),
            "disabled": r.get("disabled", False), "private": r.get("private", False),
            "fork": r.get("fork", False), "size_kb": r.get("size", 0),
            "license": (r.get("license") or {}).get("spdx_id"),
            "stars": r.get("stargazers_count"), "homepage": r.get("homepage"),
            "visibility": r.get("visibility"), "active_star": True
        })
    slugs: dict[str, str] = {}
    identities: set[str] = set()
    for record in records:
        name = record["full_name"]
        try:
            disk_slug = repo_slug(name)
        except ValueError as exc:
            raise RuntimeError(f"GitHub returned an unsafe repository name {name!r}; no catalog changed") from exc
        canonical = name.casefold()
        if canonical in identities:
            raise RuntimeError(f"GitHub returned duplicate repository identity {name!r}; no catalog changed")
        identities.add(canonical)
        slug_key = disk_slug.casefold()
        previous = slugs.get(slug_key)
        if previous and previous.casefold() != canonical:
            raise RuntimeError(f"Repository slug collision between {previous!r} and {name!r}; no catalog changed")
        slugs[slug_key] = name
    names = {r["full_name"].casefold() for r in records}
    complete = before == after == len(names) == len(records)
    snapshot = {
        "schema_version": 1, "user": user,
        "synced_at": datetime.now(timezone.utc).isoformat(),
        "expected_count_before": before, "expected_count_after": after,
        "count": len(records), "complete": complete, "pages": pages,
        "source": "Authenticated GitHub REST /user/starred with star+json; GraphQL viewer.starredRepositories.totalCount",
        "repositories": records
    }
    if not complete:
        (data / "catalog.incomplete.json").write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8")
        raise RuntimeError(f"Star count changed or list incomplete: before={before}, after={after}, unique={len(names)}. Existing catalog preserved.")
    prior_path = data / "catalog.json"
    prior = json.loads(prior_path.read_text(encoding="utf-8")) if prior_path.exists() else {"repositories": []}
    prior_names = {r["full_name"] for r in prior["repositories"]}
    current_names = {r["full_name"] for r in records}
    snapshot["changes"] = {"added": sorted(current_names - prior_names), "removed": sorted(prior_names - current_names)}
    history = data / "catalog-history"
    history.mkdir(exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    (history / f"{stamp}.json").write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8")
    pending = data / "catalog.tmp.json"
    pending.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8")
    pending.replace(data / "catalog.json")
    print(json.dumps({"user": user, "count": len(records), "complete": complete, "path": str(data / "catalog.json")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
