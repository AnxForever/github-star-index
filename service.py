"""Read-only application service for the local GitHub Star index.

The service deliberately delegates ranking and repository detail rendering to
``search.py``. Listing and public-only aggregate views use read-only SQLite
queries so filters are applied before pagination and private rows fail closed.
"""
from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path
from typing import Any

import search as _search


ROOT = Path(__file__).resolve().parent
_FILTER_TEXT_RE = re.compile(r"[\x00-\x1f\x7f]")
_DEMO_URL_RE = re.compile(r"https?://\S+", re.IGNORECASE)
_SENSITIVE_KEYS = {
    "localpath", "credential", "credentials", "token", "accesstoken",
    "refreshtoken", "apikey", "secret", "password", "privatekey",
}


def _clean_public_value(value: Any) -> Any:
    """Remove machine-local paths and obvious credential fields recursively."""
    if isinstance(value, dict):
        clean: dict[str, Any] = {}
        for key, item in value.items():
            normalized = re.sub(r"[^a-z0-9]", "", str(key).casefold())
            if normalized in _SENSITIVE_KEYS:
                continue
            clean[key] = _clean_public_value(item)
        return clean
    if isinstance(value, list):
        return [_clean_public_value(item) for item in value]
    return value


def _clean_demo_value(value: Any) -> Any:
    """Prevent synthetic repository names, commits, or evidence from linking out."""
    if isinstance(value, dict):
        clean: dict[str, Any] = {}
        for key, item in value.items():
            normalized = re.sub(r"[^a-z0-9]", "", str(key).casefold())
            if normalized in {"url", "sourceurl", "sourcesha", "cardsourcesha", "manifestsourcesha"}:
                clean[key] = None
            else:
                clean[key] = _clean_demo_value(item)
        return clean
    if isinstance(value, list):
        return [_clean_demo_value(item) for item in value]
    if isinstance(value, str):
        return _DEMO_URL_RE.sub("[demo link omitted]", value)
    return value


def _filter_text(value: str | None, field: str, *, max_length: int = 120) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"{field} must be a string")
    value = value.strip()
    if not value:
        return None
    if len(value) > max_length or _FILTER_TEXT_RE.search(value):
        raise ValueError(f"{field} is too long or contains control characters")
    return value


def _positive_int(value: int, field: str, *, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{field} must be an integer")
    try:
        number = int(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(f"{field} must be an integer") from exc
    if number < minimum or number > maximum:
        raise ValueError(f"{field} must be between {minimum} and {maximum}")
    return number


def _selection_card_status(row: sqlite3.Row, card: Any) -> str:
    """Return the same freshness state as show_repo, failing closed on bad rows."""
    if not row["card_json"]:
        return "missing"
    if not isinstance(card, dict):
        return "unfinalized"
    if row["card_stale"] == 1:
        return "stale"
    if row["card_stale"] is None:
        return "freshness_unknown"
    if row["card_ready"] != 1:
        return "unfinalized"
    if card.get("full_name") != row["full_name"]:
        return "unfinalized"
    if card.get("source_sha") != row["card_source_sha"]:
        return "unfinalized"
    return "current"


def _card_strings(card: dict[str, Any], key: str, *, limit: int = 3) -> list[str]:
    """Project only short string lists from the explicitly reviewed card fields."""
    value = card.get(key)
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str) and item.strip()][:limit]


def _card_evidence_view(full_name: str, source_sha: str | None, evidence: Any) -> dict[str, Any] | None:
    """Expose a small allowlisted citation; never pass card-local paths through."""
    if not isinstance(evidence, dict):
        return None
    path = evidence.get("path")
    start = evidence.get("start_line")
    end = evidence.get("end_line")
    if not isinstance(path, str):
        return None
    url = _search.evidence_url(full_name, source_sha, path, start, end)
    if not url:
        return None
    view = {
        "id": evidence.get("id") if isinstance(evidence.get("id"), str) else None,
        "claim": evidence.get("claim") if isinstance(evidence.get("claim"), str) else None,
        "path": path,
        "start_line": start if isinstance(start, int) and not isinstance(start, bool) else None,
        "end_line": end if isinstance(end, int) and not isinstance(end, bool) else None,
        "excerpt": evidence.get("excerpt") if isinstance(evidence.get("excerpt"), str) else None,
        "url": url,
        "source_sha": source_sha,
    }
    return _clean_public_value(view)


def _matched_card_features(item: dict[str, Any], card: dict[str, Any], full_name: str) -> list[dict[str, Any]]:
    """Map only an exact feature-document hit to features sharing direct evidence."""
    match = item.get("match")
    if not isinstance(match, dict) or match.get("type") != "feature":
        return []
    title = match.get("title")
    if not isinstance(title, str) or not title:
        return []

    matched_evidence_ids = {
        evidence.get("id")
        for evidence in item.get("evidence", [])
        if isinstance(evidence, dict)
        and evidence.get("match_context") == "matched document"
        and isinstance(evidence.get("id"), str)
    }
    if not matched_evidence_ids:
        return []

    card_evidence = card.get("evidence")
    if not isinstance(card_evidence, list):
        return []
    evidence_by_id = {
        evidence.get("id"): evidence
        for evidence in card_evidence
        if isinstance(evidence, dict) and isinstance(evidence.get("id"), str)
    }

    output: list[dict[str, Any]] = []
    features = card.get("features")
    for feature in features if isinstance(features, list) else []:
        if not isinstance(feature, dict) or feature.get("name") != title:
            continue
        level = feature.get("evidence_level")
        # Metadata claims are not shown as evidence-backed feature matches.
        if level not in {"readme", "docs", "source"}:
            continue
        refs = feature.get("evidence")
        if not isinstance(refs, list):
            continue
        cited_ids = [ref for ref in refs if isinstance(ref, str) and ref in evidence_by_id]
        matched_ids = [ref for ref in cited_ids if ref in matched_evidence_ids]
        if not matched_ids:
            continue
        citations = [
            view for ref in matched_ids
            if (view := _card_evidence_view(full_name, card.get("source_sha"), evidence_by_id[ref])) is not None
        ][:3]
        if not citations:
            continue
        output.append({
            "name": title,
            "description": feature.get("description") if isinstance(feature.get("description"), str) else "",
            "evidence_level": level,
            "evidence": citations,
        })
        if len(output) >= 3:
            break
    return output


def _selection_brief(item: dict[str, Any], row: sqlite3.Row | None) -> dict[str, Any]:
    """Build UI decision context without deriving claims from lexical rank."""
    card: Any = None
    if row is not None and row["card_json"]:
        try:
            card = json.loads(row["card_json"])
        except (TypeError, json.JSONDecodeError):
            card = None
    status = _selection_card_status(row, card) if row is not None else "missing"
    current = status == "current" and isinstance(card, dict)

    match = item.get("match") if isinstance(item.get("match"), dict) else {}
    match_type = match.get("type")
    metadata_only = match_type == "metadata" or (row is not None and row["evidence_level"] == "metadata")
    purpose = card.get("summary_zh") if current and isinstance(card.get("summary_zh"), str) else None
    matched_features = _matched_card_features(item, card, item["full_name"]) if current and not metadata_only else []
    if status in {"stale", "freshness_unknown", "unfinalized"}:
        candidate_state = "stale_or_unfinalized"
    elif status == "missing":
        candidate_state = "no_current_card"
    elif metadata_only:
        candidate_state = "metadata_only"
    elif matched_features:
        candidate_state = "feature_evidence_match"
    elif match_type == "card":
        candidate_state = "task_summary_match"
    elif match_type == "metadata":
        candidate_state = "metadata_only"
    else:
        candidate_state = "matched_excerpt_only"

    coverage = item.get("lexical_coverage")
    missing = coverage.get("missing", []) if isinstance(coverage, dict) else []
    missing_conditions = [
        {"condition": condition, "status": "unknown"}
        for condition in missing if isinstance(condition, str) and condition.strip()
    ] if isinstance(missing, list) else []

    excerpt = match.get("excerpt")
    if isinstance(excerpt, str) and excerpt.strip():
        basis = "matched_excerpt"
    elif purpose:
        basis = "card_summary"
    else:
        basis = "none"

    raw_card = card if isinstance(card, dict) else {}
    runtime_verified = raw_card.get("runtime_verified")
    analysis_date = raw_card.get("analyzed_at")
    brief = {
        "purpose": purpose,
        "matched_features": matched_features,
        "requirements": _card_strings(card, "requirements") if current else [],
        "limitations": _card_strings(card, "limitations") if current else [],
        "unverified": _card_strings(card, "unverified") if current else [],
        "card_status": status,
        "runtime_verified": runtime_verified if isinstance(runtime_verified, bool) else None,
        "analysis_date": analysis_date if isinstance(analysis_date, str) else None,
        "missing_conditions": missing_conditions,
        "basis": basis,
        "candidate_state": candidate_state,
    }
    return _clean_public_value(brief)


class IndexService:
    """Expose bounded, read-only operations over one local index."""

    def __init__(
        self,
        root: str | Path | None = None,
        db_path: str | Path | None = None,
        public_only: bool = False,
    ) -> None:
        self.root = Path(root or ROOT).expanduser().resolve()
        self.db_path = Path(db_path).expanduser() if db_path is not None else None
        self.public_only = bool(public_only)
        self.mode_override: str | None = None

    @property
    def mode(self) -> str:
        return self.mode_override or ("public" if self.public_only else "local")

    def _index_path(self) -> Path:
        return _search._db_path(self.root, self.db_path)

    def _connect(self) -> sqlite3.Connection:
        return _search._connect(self._index_path())

    def info(self) -> dict[str, Any]:
        """Return catalog status, safe filter choices, and current visibility mode."""
        if not self.public_only:
            stats = _search.stats(root=self.root, db_path=self.db_path)
            conn = self._connect()
            try:
                kinds = [row[0] for row in conn.execute(
                    "SELECT DISTINCT kind FROM repositories WHERE kind IS NOT NULL AND trim(kind)<>'' ORDER BY lower(kind),kind"
                )]
                languages = [row[0] for row in conn.execute(
                    "SELECT DISTINCT language FROM repositories WHERE language IS NOT NULL AND trim(language)<>'' ORDER BY lower(language),language"
                )]
                evidence_levels = [row[0] for row in conn.execute(
                    "SELECT DISTINCT evidence_level FROM repositories WHERE evidence_level IS NOT NULL ORDER BY evidence_level"
                )]
            finally:
                conn.close()
        else:
            stats = self._public_stats()
            conn = self._connect()
            try:
                kinds = [row[0] for row in conn.execute(
                    "SELECT DISTINCT kind FROM repositories WHERE private=0 AND kind IS NOT NULL AND trim(kind)<>'' ORDER BY lower(kind),kind"
                )]
                languages = [row[0] for row in conn.execute(
                    "SELECT DISTINCT language FROM repositories WHERE private=0 AND language IS NOT NULL AND trim(language)<>'' ORDER BY lower(language),language"
                )]
                evidence_levels = [row[0] for row in conn.execute(
                    "SELECT DISTINCT evidence_level FROM repositories WHERE private=0 AND evidence_level IS NOT NULL ORDER BY evidence_level"
                )]
            finally:
                conn.close()
        return {
            "stats": stats,
            "choices": {
                "kinds": kinds,
                "languages": languages,
                "evidence_levels": evidence_levels,
            },
            "mode": self.mode,
        }

    def _public_stats(self) -> dict[str, Any]:
        """Compute aggregate values only over confirmed-public rows."""
        conn = self._connect()
        try:
            meta = {row["key"]: row["value"] for row in conn.execute("SELECT key,value FROM index_meta")}
            repo_count = conn.execute("SELECT COUNT(*) FROM repositories WHERE private=0").fetchone()[0]
            archived = conn.execute("SELECT COUNT(*) FROM repositories WHERE private=0 AND archived=1").fetchone()[0]
            level_rows = conn.execute(
                "SELECT evidence_level,COUNT(*) AS count FROM repositories WHERE private=0 GROUP BY evidence_level ORDER BY evidence_level"
            ).fetchall()
            kind_rows = conn.execute(
                "SELECT kind,COUNT(*) AS count FROM repositories WHERE private=0 GROUP BY kind ORDER BY kind"
            ).fetchall()
            stale = conn.execute("SELECT COUNT(*) FROM repositories WHERE private=0 AND card_stale=1").fetchone()[0]
            current = conn.execute("SELECT COUNT(*) FROM repositories WHERE private=0 AND card_stale=0").fetchone()[0]
            unknown = conn.execute("SELECT COUNT(*) FROM repositories WHERE private=0 AND card_stale IS NULL").fetchone()[0]
            ready = conn.execute("SELECT COUNT(*) FROM repositories WHERE private=0 AND card_ready=1").fetchone()[0]
            unfinalized = conn.execute(
                "SELECT COUNT(*) FROM repositories WHERE private=0 AND card_stale=0 AND card_ready=0"
            ).fetchone()[0]
            cards = conn.execute("SELECT COUNT(*) FROM repositories WHERE private=0 AND card_json IS NOT NULL").fetchone()[0]
            snapshots = conn.execute(
                "SELECT COUNT(*) FROM repositories WHERE private=0 AND manifest_source_sha IS NOT NULL"
            ).fetchone()[0]
            doc_rows = conn.execute(
                "SELECT d.doc_type,COUNT(*) AS count FROM documents d JOIN repositories r ON r.full_name=d.full_name "
                "WHERE r.private=0 GROUP BY d.doc_type ORDER BY d.doc_type"
            ).fetchall()
            return {
                "built_at": meta.get("built_at"),
                # Public aggregates describe visible entries, not the account
                # that owns the complete personal catalog.
                "catalog_user": None,
                # These values describe only confirmed-public indexed rows.
                # Do not return the private total, even as a difference.
                "catalog_count": repo_count,
                "catalog_expected_count": repo_count if meta.get("catalog_complete") == "1" else None,
                "catalog_complete": meta.get("catalog_complete") == "1",
                "source_snapshots": snapshots,
                "cards": cards,
                "repositories": repo_count,
                "archived": archived,
                "stale_cards": stale,
                "current_cards": current,
                "ready_cards": ready,
                "unfinalized_cards": unfinalized,
                "card_freshness_unknown": unknown,
                "evidence_levels": {row["evidence_level"]: row["count"] for row in level_rows},
                "kinds": {row["kind"] or "unclassified": row["count"] for row in kind_rows},
                "indexed_documents": {row["doc_type"]: row["count"] for row in doc_rows},
            }
        finally:
            conn.close()

    def search(
        self,
        query: str,
        limit: int = 12,
        kind: str | None = None,
        language: str | None = None,
        archived: bool | None = None,
        evidence_level: str | None = None,
        extra_terms: list[str] | None = None,
    ) -> dict[str, Any]:
        if not isinstance(query, str) or not query.strip() or len(query) > 2000:
            raise ValueError("query must be a non-empty string of at most 2000 characters")
        limit = _positive_int(limit, "limit", minimum=1, maximum=100)
        kind = _filter_text(kind, "kind")
        language = _filter_text(language, "language")
        evidence_level = _filter_text(evidence_level, "evidence_level")
        if archived is not None and not isinstance(archived, bool):
            raise ValueError("archived must be true, false, or null")
        if extra_terms is None:
            extra_terms = []
        if not isinstance(extra_terms, list) or len(extra_terms) > 20 or any(
            not isinstance(term, str) or len(term) > 128 for term in extra_terms
        ):
            raise ValueError("extra_terms must contain at most 20 short strings")
        result = _search.search_repositories(
            query.strip(),
            root=self.root,
            db_path=self.db_path,
            limit=limit,
            kind=kind,
            language=language,
            archived=archived,
            evidence_level=evidence_level,
            extra_terms=extra_terms,
            include_private=not self.public_only,
        )
        results = result.get("results", [])
        rows_by_name: dict[str, sqlite3.Row] = {}
        names = [item.get("full_name") for item in results if isinstance(item, dict) and isinstance(item.get("full_name"), str)]
        if names:
            placeholders = ",".join("?" for _ in names)
            visibility_clause = " AND private=0" if self.public_only else ""
            conn = self._connect()
            try:
                rows = conn.execute(
                    "SELECT full_name,private,card_json,card_stale,card_ready,card_source_sha,manifest_source_sha,evidence_level "
                    f"FROM repositories WHERE full_name IN ({placeholders}){visibility_clause}",
                    names,
                ).fetchall()
                rows_by_name = {row["full_name"].casefold(): row for row in rows}
            finally:
                conn.close()

        for item in results:
            if not isinstance(item, dict):
                continue
            item["selection_brief"] = _selection_brief(
                item, rows_by_name.get(item.get("full_name", "").casefold())
                if isinstance(item.get("full_name"), str) else None,
            )
            coverage = item.get("lexical_coverage") or {}
            evidence = item.get("evidence") or []
            matched = sum(1 for entry in evidence if entry.get("match_context") == "matched document")
            related = sum(1 for entry in evidence if entry.get("match_context") == "repository card evidence")
            item["match_explanation"] = {
                "method": "SQLite FTS5 lexical retrieval with bilingual concept coverage",
                "matched_concepts": coverage.get("matched", []),
                "missing_concepts": coverage.get("missing", []),
                "evidence_from_matched_documents": matched,
                "related_repository_evidence": related,
                "note": "Related repository evidence provides context; it is not itself proof that the matched feature implements the query.",
            }
        return _clean_demo_value(result) if self.mode == "demo" else result

    def show(self, full_name: str) -> dict[str, Any]:
        full_name = _filter_text(full_name, "name", max_length=255) or ""
        if full_name.count("/") != 1:
            raise ValueError("name must use the owner/repository form")
        if self.public_only:
            conn = self._connect()
            try:
                row = conn.execute(
                    "SELECT private FROM repositories WHERE lower(full_name)=lower(?) AND private=0",
                    (full_name,),
                ).fetchone()
            finally:
                conn.close()
            if row is None:
                raise _search.IndexErrorBase("Repository not found in this index")
        result = _search.show_repo(full_name, root=self.root, db_path=self.db_path)
        result = _clean_public_value(result)
        return _clean_demo_value(result) if self.mode == "demo" else result

    def compare(self, repositories: list[str]) -> dict[str, Any]:
        if not isinstance(repositories, list) or not 1 <= len(repositories) <= 4:
            raise ValueError("repositories must contain between 1 and 4 owner/repository names")
        cleaned: list[str] = []
        seen: set[str] = set()
        for repository in repositories:
            name = _filter_text(repository, "repository", max_length=255)
            if name is None or name.count("/") != 1:
                raise ValueError("each repository must use the owner/repository form")
            folded = name.casefold()
            if folded in seen:
                raise ValueError("repositories must be unique")
            seen.add(folded)
            cleaned.append(name)
        return {"repositories": [self.show(name) for name in cleaned]}

    def list(
        self,
        limit: int = 12,
        offset: int = 0,
        kind: str | None = None,
        language: str | None = None,
        archived: bool | None = None,
        evidence_level: str | None = None,
    ) -> dict[str, Any]:
        limit = _positive_int(limit, "limit", minimum=1, maximum=100)
        if isinstance(offset, bool) or not isinstance(offset, int):
            raise ValueError("offset must be a non-negative integer")
        try:
            offset = int(offset)
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError("offset must be a non-negative integer") from exc
        if offset < 0 or offset > 10_000_000:
            raise ValueError("offset must be between 0 and 10000000")
        kind = _filter_text(kind, "kind")
        language = _filter_text(language, "language")
        evidence_level = _filter_text(evidence_level, "evidence_level")
        if archived is not None and not isinstance(archived, bool):
            raise ValueError("archived must be true, false, or null")

        clauses: list[str] = []
        params: list[Any] = []
        if self.public_only:
            clauses.append("private=0")
        if kind:
            clauses.append("lower(kind)=lower(?)")
            params.append(kind)
        if language:
            clauses.append("lower(language)=lower(?)")
            params.append(language)
        if archived is not None:
            clauses.append("archived=?")
            params.append(int(archived))
        if evidence_level:
            clauses.append("evidence_level=?")
            params.append(evidence_level)
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""

        conn = self._connect()
        try:
            total = conn.execute("SELECT COUNT(*) FROM repositories" + where, params).fetchone()[0]
            rows = conn.execute(
                "SELECT * FROM repositories" + where + " ORDER BY full_name COLLATE NOCASE LIMIT ? OFFSET ?",
                [*params, limit, offset],
            ).fetchall()
            results = [_search._repo_row(row) for row in rows]
            if self.mode == "demo":
                results = [_clean_demo_value(item) for item in results]
            return {
                "count": len(results),
                "total": total,
                "limit": limit,
                "offset": offset,
                "results": results,
            }
        finally:
            conn.close()

