"""Local, evidence-aware search over a GitHub star catalog.

The index uses SQLite FTS5 with lexical matching. Chinese runs are represented
by overlapping character bigrams so substring queries remain useful without an
external tokenizer or embedding service.
"""
from __future__ import annotations

import argparse
import html
import json
import os
import re
import sqlite3
import sys
import tempfile
import unicodedata
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath, PureWindowsPath
from urllib.parse import quote

ROOT = Path(__file__).resolve().parent
_CJK_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\U00020000-\U000323af]+")
_TOKEN_RE = re.compile(
    r"[A-Za-z0-9_+#.-]+|[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\U00020000-\U000323af]+|[^\W_]+",
    re.UNICODE,
)
_SHA_RE = re.compile(r"^[0-9a-fA-F]{40}$")
_REPO_PART_RE = re.compile(r"^[A-Za-z0-9_.-]+$")
_CARD_KINDS = {"app", "library", "framework", "cli", "skill", "collection", "tutorial", "research", "infrastructure", "other"}
_EVIDENCE_LEVELS = {"metadata", "readme", "docs", "source"}
_DOCUMENT_EXTENSIONS = {".md", ".mdx", ".rst", ".txt", ".pdf"}
_EN_STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "be", "can", "do", "does", "for", "from",
    "how", "i", "in", "is", "it", "me", "my", "of", "on", "or", "the", "to", "use",
    "with", "what", "which", "that", "this", "want", "would",
}
_SYNONYMS: dict[str, tuple[str, ...]] = {
    "检索": ("search", "retrieval"),
    "搜索": ("search",),
    "仓库": ("repository", "repo", "project"),
    "项目": ("project", "repository"),
    "代码": ("code", "source"),
    "源码": ("source", "code"),
    "文档": ("documentation", "docs", "document"),
    "知识库": ("knowledge base", "rag"),
    "问答": ("question answering", "qa"),
    "工作流": ("workflow", "pipeline"),
    "自动化": ("automation",),
    "智能体": ("agent",),
    "向量": ("vector", "embedding"),
    "图片": ("image", "image generation"),
    "图像": ("image", "image generation"),
    "视频": ("video",),
    "音频": ("audio", "speech"),
    "聊天": ("chat", "conversation"),
    "部署": ("deploy", "deployment"),
    "界面": ("ui", "interface"),
    "组件": ("component",),
    "爬虫": ("crawler", "scraper", "scraping"),
    "数据集": ("dataset",),
    "本地": ("local",),
    "命令行": ("cli", "command line"),
    "接口": ("api", "interface"),
    "模型": ("model", "llm"),
    "测试": ("test", "testing"),
}
# Query concepts provide lexical alternatives for common Chinese task terms.
# Each concept is scored as one group: a repository must match at least one
# alias in the group, which prevents a query such as "声音克隆" from ranking a
# project highly merely because its README says "git clone".
_CONCEPTS: dict[str, tuple[tuple[str, ...], float]] = {
    "模型供应商": (("model provider", "model providers", "llm provider", "vendor"), 1.35),
    "可交互": (("interactive", "interact", "interactivity"), 1.2),
    "工作流": (("workflow", "workflows", "pipeline", "orchestration"), 1.1),
    "声音": (("voice", "audio", "speech", "vocal"), 1.0),
    "克隆": (("clone", "cloning", "cloned"), 1.1),
    "本地": (("local", "offline", "self-hosted", "self hosted"), 0.8),
    "知识库": (("knowledge base", "knowledge-base", "rag"), 1.0),
    "文档": (("document", "documents", "documentation", "docs"), 0.75),
    "问答": (("question answering", "question answer", "qa", "rag"), 1.0),
    "论文": (("research paper", "academic paper", "paper", "papers", "article"), 1.0),
    "网页": (("webpage", "web page", "website", "web", "html"), 0.8),
    "动画": (("animation", "animations", "animated", "motion"), 1.1),
    "组件": (("component", "components"), 0.9),
    "编程": (("coding", "programming", "code", "developer"), 1.0),
    "切换": (("switch", "switching", "routing", "route", "select"), 1.0),
    "供应商": (("provider", "providers", "vendor", "vendors"), 1.2),
    "模型": (("model", "models", "llm"), 0.9),
    "交互": (("interactive", "interact", "interactivity"), 0.8),
    "界面": (("ui", "interface", "frontend"), 0.7),
    "向量数据库": (("vector database", "vector db", "vector store", "vector search", "embedding store", "embedding database", "vector index"), 1.3),
    "数据库": (("database", "db", "sql", "sqlite", "postgres", "postgresql", "mysql"), 0.95),
    "转录": (("transcribe", "transcription", "transcript", "speech to text", "speech-to-text", "asr", "caption"), 1.2),
    "语音合成": (("text to speech", "text-to-speech", "tts", "speech synthesis", "voice synthesis"), 1.2),
    "爬虫": (("crawler", "crawling", "scraper", "scraping", "spider"), 1.15),
    "自动化": (("automation", "automate", "automated", "scripting"), 1.0),
    "检索": (("search", "retrieval", "retrieve", "lookup"), 0.9),
    "聊天": (("chat", "chatbot", "conversation", "conversational", "messaging"), 0.9),
    "部署": (("deploy", "deployment", "self-host", "self hosted", "docker"), 0.85),
    "提示词": (("prompt", "prompts", "system prompt"), 0.8),
    "技能": (("skill", "skills"), 0.75),
    "微调": (("fine-tune", "fine tuning", "finetune", "lora", "training"), 0.9),
    "评测": (("benchmark", "evaluation", "eval", "leaderboard"), 0.85),
    "工具": (("tool", "tools", "toolkit", "cli", "application"), 0.45),
    "变成": (("convert", "conversion", "transform", "generate", "turn into"), 0.45),
    "生成": (("generate", "generation", "create"), 0.55),
}
_CONCEPT_KEYS = tuple(sorted(_CONCEPTS, key=len, reverse=True))
_CJK_STOP_CHARS = {"把", "的", "和", "为", "与", "从", "在", "用", "我", "想", "找", "能", "可以"}
_TEXT_EXTENSIONS = {".md", ".mdx", ".rst", ".txt", ".adoc", ".org"}
_MAX_EXCERPT_CHARS = 340
_DOC_CHUNK_CHARS = 12_000


class IndexErrorBase(RuntimeError):
    """Expected catalog/index error suitable for a concise CLI message."""


def _json_file(path: Path) -> dict | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _schema(root: Path) -> dict:
    value = _json_file(root / "schema.json")
    if value is None:
        raise IndexErrorBase(f"Cannot read schema.json under {root}")
    return value


def _db_path(root: Path, requested: str | Path | None) -> Path:
    if requested is None:
        requested = _schema(root).get("database", "data/index.sqlite3")
    path = Path(requested)
    return path if path.is_absolute() else root / path


def _cjk_run_terms(run: str) -> list[str]:
    run = run.casefold()
    if len(run) == 1:
        return [run]
    return [run[i : i + 2] for i in range(len(run) - 1)]


def text_terms(value: str) -> list[str]:
    """Split text into English words and CJK bigrams, preserving order."""
    value = unicodedata.normalize("NFC", value).casefold()
    result: list[str] = []
    for match in _TOKEN_RE.finditer(value):
        token = match.group(0).strip("_+#.-")
        if not token:
            continue
        if _CJK_RE.fullmatch(token):
            result.extend(_cjk_run_terms(token))
        elif any(ch.isalnum() for ch in token):
            result.append(token)
    return result


def _fts_text(value: str) -> str:
    return " ".join(text_terms(value))


def query_terms(query: str, extra_terms: list[str] | None = None) -> list[str]:
    """Tokenize a query and add a small bilingual synonym expansion."""
    combined = query
    if extra_terms:
        combined += " " + " ".join(extra_terms)
    terms = text_terms(combined)
    folded = unicodedata.normalize("NFC", combined).casefold()
    for key, translations in _SYNONYMS.items():
        if key in folded:
            for translation in translations:
                terms.extend(text_terms(translation))
    seen: set[str] = set()
    filtered: list[str] = []
    for term in terms:
        if term in _EN_STOPWORDS or term in seen:
            continue
        seen.add(term)
        filtered.append(term)
        if len(filtered) >= 96:
            break
    return filtered


def query_plan(query: str, extra_terms: list[str] | None = None) -> tuple[list[str], list[dict]]:
    """Return FTS terms and weighted concept groups for lexical ranking."""
    combined = query + (" " + " ".join(extra_terms) if extra_terms else "")
    terms = query_terms(query, extra_terms)
    groups: list[dict] = []

    def add_group(aliases: tuple[str, ...] | list[str], weight: float, label: str) -> None:
        alternatives = []
        for alias in aliases:
            alias_terms = tuple(text_terms(alias))
            if alias_terms:
                alternatives.append(alias_terms)
                terms.extend(alias_terms)
        if alternatives:
            groups.append({"label": label, "alternatives": alternatives, "weight": weight})

    folded = unicodedata.normalize("NFC", combined).casefold()
    for match in _TOKEN_RE.finditer(folded):
        token = match.group(0).strip("_+#.-")
        if not token:
            continue
        if _CJK_RE.fullmatch(token):
            index = 0
            while index < len(token):
                concept = next((key for key in _CONCEPT_KEYS if token.startswith(key, index)), None)
                if concept:
                    aliases, weight = _CONCEPTS[concept]
                    add_group((concept, *aliases), weight, concept)
                    index += len(concept)
                    continue
                # Preserve unknown Chinese wording as low-weight exact bigram
                # evidence. Known natural-language filler is ignored.
                if token[index] in _CJK_STOP_CHARS:
                    index += 1
                    continue
                run_end = index + 1
                while run_end < len(token) and not any(token.startswith(key, run_end) for key in _CONCEPT_KEYS):
                    if token[run_end] in _CJK_STOP_CHARS:
                        break
                    run_end += 1
                segment = token[index:run_end]
                if segment:
                    if len(segment) == 1:
                        add_group((segment,), 0.18, segment)
                    elif len(segment) == 2:
                        add_group((segment,), 0.24, segment)
                    else:
                        # An unknown multi-character Chinese phrase has no
                        # synonym list, so a single conjunctive group would
                        # demand every overlapping bigram at once and reject
                        # virtually every real document (for example
                        # “向量数据库检索” matched nothing). Emit one low-weight
                        # group per bigram instead, so a phrase still scores
                        # proportionally to how much of it appears.
                        for offset in range(len(segment) - 1):
                            add_group((segment[offset : offset + 2],), 0.2, segment[offset : offset + 2])
                index = max(run_end, index + 1)
        elif any(ch.isalnum() for ch in token):
            term = token.casefold()
            if term in _EN_STOPWORDS:
                continue
            weight = 0.35 if term in {"ai", "llm", "tool", "tools", "app", "application"} else 0.9
            add_group((term,), weight, term)

    seen_groups: set[tuple] = set()
    unique_groups = []
    for group in groups:
        key = (group["label"], tuple(group["alternatives"]))
        if key not in seen_groups:
            seen_groups.add(key)
            unique_groups.append(group)
    terms = list(dict.fromkeys(term for term in terms if term and term not in _EN_STOPWORDS))[:160]
    return terms, unique_groups


def _group_coverage(content: str, groups: list[dict], *, window_lines: int | None = None) -> tuple[float, int, int]:
    if not groups:
        return 1.0, 0, 0
    total_weight = sum(group["weight"] for group in groups)

    def score(text: str) -> tuple[float, int, int]:
        available = set(text_terms(text))
        matched_weight = 0.0
        matched_count = 0
        for group in groups:
            if any(all(term in available for term in alternative) for alternative in group["alternatives"]):
                matched_weight += group["weight"]
                matched_count += 1
        return (matched_weight / total_weight if total_weight else 0.0, matched_count, len(groups))

    if not window_lines:
        return score(content)
    lines = [line for line in content.splitlines() if line.strip()]
    if len(lines) <= window_lines:
        return score("\n".join(lines))
    # A whole README chunk can contain unrelated sections. Use its strongest
    # short passage for ranking so distant mentions do not combine into a
    # misleading full-coverage match.
    return max((score("\n".join(lines[start : start + window_lines]))
                for start in range(len(lines) - window_lines + 1)), key=lambda item: (item[0], item[1]))


def _visible_text(content: str) -> str:
    """Remove markup and URL attributes before judging visible README text."""
    visible_lines = []
    for line in content.splitlines():
        line = html.unescape(line)
        line = re.sub(r"<!--.*?-->", " ", line)
        line = re.sub(r"!?\[([^\]]*)\]\([^)]*\)", r"\1", line)
        line = re.sub(r"<[^>]*>", " ", line)
        line = re.sub(r"https?://\S+", " ", line, flags=re.IGNORECASE)
        visible_lines.append(line)
    return "\n".join(visible_lines)


def _coverage_labels(content: str, groups: list[dict], *, window_lines: int | None = None) -> tuple[list[str], list[str]]:
    if not groups:
        return [], []
    candidates = [content]
    if window_lines:
        lines = [line for line in content.splitlines() if line.strip()]
        candidates = ["\n".join(lines[start : start + window_lines])
                      for start in range(max(1, len(lines) - window_lines + 1))]
    best = max(candidates, key=lambda text: _group_coverage(text, groups)[:2])
    available = set(text_terms(best))
    matched = [group["label"] for group in groups
               if any(all(term in available for term in alternative) for alternative in group["alternatives"])]
    missing = [group["label"] for group in groups if group["label"] not in matched]
    return matched, missing


def _fts_expression(terms: list[str]) -> str:
    if not terms:
        raise ValueError("query has no searchable words")
    # Terms come from the tokenizer, but quoting each one also prevents FTS
    # operators or punctuation in user input from changing query semantics.
    return " OR ".join('"' + term.replace('"', '""') + '"' for term in terms)


def _safe_repo_name(full_name: str) -> bool:
    parts = full_name.split("/")
    return len(parts) == 2 and all(
        part not in {"", ".", ".."} and _REPO_PART_RE.fullmatch(part) is not None for part in parts
    )


def safe_repo_path(path: str) -> bool:
    """Reject absolute paths and traversal in a remote repository path."""
    if not isinstance(path, str) or not path or "\x00" in path or "\\" in path:
        return False
    if path.startswith("/") or PurePosixPath(path).is_absolute():
        return False
    parts = path.split("/")
    return all(part not in {"", ".", ".."} for part in parts)


def _safe_local_path(root: Path, local_path: str) -> Path | None:
    if not isinstance(local_path, str) or not local_path or "\x00" in local_path or "\\" in local_path:
        return None
    if PurePosixPath(local_path).is_absolute() or PureWindowsPath(local_path).is_absolute() or PureWindowsPath(local_path).drive:
        return None
    try:
        resolved_root = root.resolve()
        candidate = (resolved_root / Path(*PurePosixPath(local_path).parts)).resolve()
        candidate.relative_to(resolved_root)
    except (OSError, ValueError):
        return None
    return candidate


def evidence_url(full_name: str, source_sha: str | None, path: str, start_line: int | None, end_line: int | None) -> str | None:
    """Make a GitHub link only when repo, commit and path are safe."""
    if not _safe_repo_name(full_name) or not isinstance(source_sha, str) or not _SHA_RE.fullmatch(source_sha):
        return None
    if not safe_repo_path(path):
        return None
    owner, repo = full_name.split("/")
    url = f"https://github.com/{quote(owner, safe='')}/{quote(repo, safe='')}/blob/{source_sha.lower()}/{quote(path, safe='/')}"
    if isinstance(start_line, int) and not isinstance(start_line, bool) and start_line > 0:
        url += f"#L{start_line}"
        if isinstance(end_line, int) and not isinstance(end_line, bool) and end_line >= start_line:
            url += f"-L{end_line}"
    return url


def _evidence_view(full_name: str, source_sha: str | None, evidence: dict) -> dict:
    path = evidence.get("path")
    path_safe = safe_repo_path(path) if isinstance(path, str) else False
    start = evidence.get("start_line")
    end = evidence.get("end_line")
    return {
        "id": evidence.get("id"),
        "claim": evidence.get("claim"),
        "path": path,
        "start_line": start,
        "end_line": end,
        "excerpt": evidence.get("excerpt"),
        "source_sha": source_sha,
        "url": evidence_url(full_name, source_sha, path, start, end) if path_safe else None,
        "path_safe": path_safe,
    }


def _doc_segments(text: str, max_chars: int = _DOC_CHUNK_CHARS) -> list[tuple[int, int, str]]:
    """Split a text file into searchable chunks while retaining line offsets."""
    lines = text.splitlines()
    if not lines:
        return []
    chunks: list[tuple[int, int, str]] = []
    chunk_lines: list[str] = []
    chunk_chars = 0
    chunk_start = 1
    for line_no, line in enumerate(lines, 1):
        # Keep pathological single-line files bounded for SQLite's tokenizer.
        parts = [line[i : i + max_chars] for i in range(0, len(line), max_chars)] or [""]
        for part_index, part in enumerate(parts):
            extra = len(part) + 1
            if chunk_lines and chunk_chars + extra > max_chars:
                chunks.append((chunk_start, line_no - 1 if part_index == 0 else line_no, "\n".join(chunk_lines)))
                chunk_lines = []
                chunk_chars = 0
                chunk_start = line_no
            chunk_lines.append(part)
            chunk_chars += extra
            if part_index < len(parts) - 1:
                chunks.append((chunk_start, line_no, "\n".join(chunk_lines)))
                chunk_lines = []
                chunk_chars = 0
                chunk_start = line_no
    if chunk_lines:
        chunks.append((chunk_start, len(lines), "\n".join(chunk_lines)))
    return chunks


def _is_indexable_doc(remote_path: str, readme_path: str | None) -> bool:
    if remote_path == readme_path:
        return True
    name = PurePosixPath(remote_path).name.casefold()
    if name.startswith("readme"):
        return True
    if PurePosixPath(remote_path).suffix.casefold() not in _TEXT_EXTENSIONS:
        return False
    parts = {part.casefold() for part in PurePosixPath(remote_path).parts[:-1]}
    return bool(parts & {"doc", "docs", "documentation"})


def _card_text(card: dict, fields: tuple[str, ...]) -> str:
    pieces: list[str] = []
    for key in fields:
        value = card.get(key)
        if isinstance(value, str) and value.strip():
            pieces.append(value.strip())
        elif isinstance(value, list):
            pieces.extend(str(item).strip() for item in value if isinstance(item, (str, int, float)) and str(item).strip())
    return "\n".join(pieces)


def _strings(value) -> list[str]:
    if isinstance(value, str):
        return [value] if value.strip() else []
    if isinstance(value, list):
        result: list[str] = []
        for item in value:
            result.extend(_strings(item))
        return result
    if isinstance(value, dict):
        result = []
        for key, item in value.items():
            if isinstance(item, (dict, list)):
                result.extend(_strings(item))
            elif isinstance(item, (str, int, float)) and str(item).strip():
                result.append(str(item))
        return result
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return [str(value)]
    return []


def _create_db(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        PRAGMA foreign_keys = ON;
        CREATE TABLE index_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE repositories (
            full_name TEXT PRIMARY KEY,
            url TEXT,
            description TEXT,
            language TEXT,
            topics_json TEXT NOT NULL,
            archived INTEGER NOT NULL,
            private INTEGER,
            visibility TEXT,
            kind TEXT,
            evidence_level TEXT NOT NULL,
            card_stale INTEGER,
            card_ready INTEGER,
            card_source_sha TEXT,
            manifest_source_sha TEXT,
            card_json TEXT,
            repository_json TEXT NOT NULL,
            doc_count INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE documents (
            id INTEGER PRIMARY KEY,
            full_name TEXT NOT NULL REFERENCES repositories(full_name),
            doc_type TEXT NOT NULL,
            title TEXT NOT NULL,
            evidence_level TEXT,
            path TEXT,
            source_sha TEXT,
            line_start INTEGER,
            line_end INTEGER,
            content TEXT NOT NULL,
            evidence_json TEXT
        );
        CREATE INDEX documents_repo_idx ON documents(full_name, id);
        CREATE VIRTUAL TABLE documents_fts USING fts5(
            full_name UNINDEXED, doc_type UNINDEXED, path UNINDEXED, source_sha UNINDEXED,
            title, content, tokenize='unicode61 remove_diacritics 2'
        );
        """
    )


def _insert_doc(
    conn: sqlite3.Connection,
    full_name: str,
    doc_type: str,
    title: str,
    content: str,
    *,
    evidence_level: str | None = None,
    path: str | None = None,
    source_sha: str | None = None,
    line_start: int | None = None,
    line_end: int | None = None,
    evidence: list[dict] | None = None,
) -> bool:
    if not isinstance(content, str) or not content.strip():
        return False
    indexed_title = _fts_text(title)
    indexed_content = _fts_text(content)
    if not indexed_title and not indexed_content:
        return False
    cursor = conn.execute(
        "INSERT INTO documents(full_name, doc_type, title, evidence_level, path, source_sha, line_start, line_end, content, evidence_json) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (full_name, doc_type, title, evidence_level, path, source_sha, line_start, line_end, content,
         json.dumps(evidence, ensure_ascii=False) if evidence else None),
    )
    conn.execute(
        "INSERT INTO documents_fts(rowid, full_name, doc_type, path, source_sha, title, content) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (cursor.lastrowid, full_name, doc_type, path or "", source_sha or "", indexed_title, indexed_content),
    )
    return True


def _source_files(root: Path, full_name: str, manifest: dict, sources_dir: Path) -> list[tuple[str, Path]]:
    if not _safe_repo_name(full_name):
        return []
    readme = manifest.get("readme_path")
    readme = readme if isinstance(readme, str) else None
    files = manifest.get("files")
    if not isinstance(files, list):
        return []
    found: dict[str, Path] = {}
    for item in files:
        if not isinstance(item, dict) or item.get("status") != "ok":
            continue
        remote_path = item.get("path")
        local_path = item.get("local_path")
        if not isinstance(remote_path, str) or not safe_repo_path(remote_path) or not _is_indexable_doc(remote_path, readme):
            continue
        local = _safe_local_path(root, local_path)
        if local is None or not local.is_file():
            continue
        found[remote_path] = local
    # Tolerate manifests that identify the README separately and omit it from
    # the file list, provided its exact safe path exists in the source snapshot.
    if readme and safe_repo_path(readme) and readme not in found:
        source_sha = manifest.get("source_sha")
        if isinstance(source_sha, str) and _SHA_RE.fullmatch(source_sha):
            local_root = sources_dir / full_name.replace("/", "__") / "snapshots" / source_sha / "files"
            try:
                relative = local_root.resolve().relative_to(root.resolve()).as_posix() + "/" + readme
            except (OSError, ValueError):
                relative = ""
            local = _safe_local_path(root, relative) if relative else None
            if local is not None and local.is_file():
                found[readme] = local
    return sorted(found.items(), key=lambda pair: pair[0].casefold())


def _card_ready(root: Path, full_name: str, card: dict | None, manifest: dict, manifest_sha: str | None) -> bool:
    """Accept only finalized cards whose evidence still matches the snapshot."""
    if not isinstance(card, dict) or not manifest_sha or card.get("source_sha") != manifest_sha:
        return False
    if card.get("full_name") != full_name or card.get("schema_version") != 1 or card.get("analysis_version") != 1:
        return False
    if card.get("kind") not in _CARD_KINDS or card.get("evidence_level") not in _EVIDENCE_LEVELS:
        return False
    if card.get("runtime_verified") is not False:
        return False
    analyzed_at = card.get("analyzed_at")
    if not isinstance(analyzed_at, str) or not analyzed_at.strip():
        return False
    try:
        datetime.fromisoformat(analyzed_at.replace("Z", "+00:00"))
    except ValueError:
        return False

    required = ("summary_zh", "use_cases", "tags", "features", "interfaces", "requirements", "limitations", "unverified")
    if any(key not in card for key in required):
        return False
    if not isinstance(card.get("summary_zh"), str) or not card["summary_zh"].strip():
        return False
    if any(not isinstance(card.get(key), list) for key in required[1:]):
        return False

    raw_evidence = card.get("evidence")
    if not isinstance(raw_evidence, list):
        return False
    files = {
        item.get("path"): item for item in manifest.get("files", [])
        if isinstance(item, dict) and item.get("status") == "ok" and isinstance(item.get("path"), str)
    } if isinstance(manifest.get("files"), list) else {}
    evidence_by_id: dict[str, dict] = {}
    evidence_paths: set[str] = set()
    for evidence in raw_evidence:
        if not isinstance(evidence, dict):
            return False
        ev_id = evidence.get("id")
        path = evidence.get("path")
        start = evidence.get("start_line")
        end = evidence.get("end_line")
        if not isinstance(ev_id, str) or not ev_id or ev_id in evidence_by_id:
            return False
        if not isinstance(path, str) or not safe_repo_path(path) or path not in files:
            return False
        if (not isinstance(start, int) or isinstance(start, bool) or
                not isinstance(end, int) or isinstance(end, bool) or
                not 1 <= start <= end or end - start > 11):
            return False
        file_item = files[path]
        local_path = file_item.get("local_path")
        if not isinstance(local_path, str) or evidence.get("local_path") != local_path:
            return False
        local_file = _safe_local_path(root, local_path)
        if local_file is None or not local_file.is_file():
            return False
        try:
            lines = local_file.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeError):
            return False
        if end > len(lines) or evidence.get("excerpt") != "\n".join(lines[start - 1:end]):
            return False
        if not isinstance(evidence.get("claim"), str) or not evidence["claim"].strip():
            return False
        if evidence.get("url") != evidence_url(full_name, manifest_sha, path, start, end):
            return False
        evidence_by_id[ev_id] = evidence
        evidence_paths.add(path)

    analysis_scope = card.get("analysis_scope")
    if not isinstance(analysis_scope, list) or analysis_scope != sorted(evidence_paths):
        return False
    if card.get("evidence_level") != "metadata" and not evidence_by_id:
        return False

    features = card.get("features")
    for feature in features:
        if not isinstance(feature, dict) or not isinstance(feature.get("name"), str) or not feature["name"].strip():
            return False
        refs = feature.get("evidence")
        feature_level = feature.get("evidence_level")
        if (not isinstance(refs, list) or not refs or
                any(not isinstance(ref, str) or ref not in evidence_by_id for ref in refs) or
                feature_level not in _EVIDENCE_LEVELS):
            return False
        cited = [evidence_by_id[ref] for ref in refs]
        if feature_level == "source" and all(PurePosixPath(ev["path"]).suffix.casefold() in _DOCUMENT_EXTENSIONS for ev in cited):
            return False
    return True


def _add_repo_documents(conn: sqlite3.Connection, root: Path, repo: dict, cards_dir: Path, sources_dir: Path) -> int:
    full_name = repo["full_name"]
    slug = full_name.replace("/", "__")
    source_base = sources_dir / slug
    manifest = _json_file(source_base / "manifest.json") or {}
    if manifest.get("full_name") not in (None, full_name):
        manifest = {}
    manifest_sha = manifest.get("source_sha")
    if not isinstance(manifest_sha, str) or not _SHA_RE.fullmatch(manifest_sha):
        manifest_sha = None
    card_path = cards_dir / f"{slug}.json"
    card = _json_file(card_path)
    if card is not None and card.get("full_name") not in (None, full_name):
        card = None
    card_sha = card.get("source_sha") if card else None
    if not isinstance(card_sha, str) or not _SHA_RE.fullmatch(card_sha):
        card_sha = None
    stale = None if card is None or manifest_sha is None else int(card_sha != manifest_sha)
    ready = _card_ready(root, full_name, card, manifest, manifest_sha)
    # Draft or stale cards remain available through show, but their claims do
    # not enter functional retrieval until the finalized evidence is verified.
    card_is_current = card is not None and stale == 0 and ready
    kind = card.get("kind") if card_is_current and isinstance(card.get("kind"), str) else None
    evidence_level = card.get("evidence_level") if card_is_current and isinstance(card.get("evidence_level"), str) else "metadata"
    card_json = json.dumps(card, ensure_ascii=False) if card is not None else None
    conn.execute(
        "UPDATE repositories SET kind=?, evidence_level=?, card_stale=?, card_ready=?, card_source_sha=?, manifest_source_sha=?, card_json=? WHERE full_name=?",
        (kind, evidence_level, stale, int(ready) if card is not None else None, card_sha, manifest_sha, card_json, full_name),
    )

    count = 0
    metadata = repo.get("description") or ""
    catalog_values = _strings({k: v for k, v in repo.items() if k not in {"id", "size_kb", "updated_at", "pushed_at", "starred_at"}})
    metadata = "\n".join(catalog_values)
    count += _insert_doc(conn, full_name, "metadata", full_name, metadata, evidence_level="metadata")

    if card_is_current:
        task_summary = _card_text(card, ("summary_zh", "use_cases", "tags"))
        if task_summary:
            count += _insert_doc(conn, full_name, "card", f"{full_name} project card", task_summary,
                                 source_sha=card_sha, evidence_level=evidence_level)
        details = _card_text(card, ("interfaces", "requirements", "limitations", "comparisons", "unverified"))
        if details:
            count += _insert_doc(conn, full_name, "card_detail", f"{full_name} requirements and limits", details,
                                 source_sha=card_sha, evidence_level=evidence_level)
        evidence_list = card.get("evidence") if isinstance(card.get("evidence"), list) else []
        evidence_by_id = {
            item.get("id"): item for item in evidence_list
            if isinstance(item, dict) and isinstance(item.get("id"), str)
        }
        for feature in card.get("features", []) if isinstance(card.get("features"), list) else []:
            if not isinstance(feature, dict):
                continue
            refs = feature.get("evidence") if isinstance(feature.get("evidence"), list) else []
            selected = [evidence_by_id[ref] for ref in refs if isinstance(ref, str) and ref in evidence_by_id]
            content = "\n".join([str(feature.get("name", "")), str(feature.get("description", ""))])
            feature_level = feature.get("evidence_level") if feature.get("evidence_level") in {"metadata", "readme", "docs", "source"} else evidence_level
            count += _insert_doc(conn, full_name, "feature", str(feature.get("name") or "功能"), content,
                                 source_sha=card_sha, evidence_level=feature_level, evidence=selected)
        for ev in evidence_list:
            if not isinstance(ev, dict):
                continue
            content = "\n".join(str(ev.get(key, "")) for key in ("claim", "excerpt"))
            count += _insert_doc(conn, full_name, "evidence", str(ev.get("claim") or ev.get("path") or "源码证据"), content,
                                 evidence_level=evidence_level,
                                 path=ev.get("path") if isinstance(ev.get("path"), str) else None,
                                 source_sha=card_sha, line_start=ev.get("start_line") if isinstance(ev.get("start_line"), int) else None,
                                 line_end=ev.get("end_line") if isinstance(ev.get("end_line"), int) else None,
                                 evidence=[ev])

    for remote_path, local_path in _source_files(root, full_name, manifest, sources_dir):
        try:
            raw = local_path.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            continue
        if "\x00" in raw:
            continue
        for line_start, line_end, segment in _doc_segments(raw):
            title = f"{PurePosixPath(remote_path).name} · {remote_path}"
            remote_name = PurePosixPath(remote_path).name.casefold()
            doc_level = "readme" if remote_name.startswith("readme") else "docs"
            count += _insert_doc(conn, full_name, "source_doc", title, segment, evidence_level=doc_level,
                                 path=remote_path, source_sha=manifest_sha,
                                 line_start=line_start, line_end=line_end)
    return count


def build_index(root: str | Path = ROOT, db_path: str | Path | None = None) -> dict:
    """Rebuild the SQLite database atomically from the current local files."""
    root_path = Path(root).resolve()
    schema = _schema(root_path)
    catalog_path = root_path / schema.get("catalog_file", "data/catalog.json")
    cards_dir = root_path / schema.get("cards_dir", "data/cards")
    sources_dir = root_path / schema.get("sources_dir", "data/sources")
    catalog = _json_file(catalog_path)
    if catalog is None or not isinstance(catalog.get("repositories"), list):
        raise IndexErrorBase(f"Cannot read a valid catalog: {catalog_path}")
    repos = catalog["repositories"]
    full_names = [repo.get("full_name") for repo in repos if isinstance(repo, dict)]
    if len(full_names) != len(repos) or any(not isinstance(name, str) or not _safe_repo_name(name) for name in full_names):
        raise IndexErrorBase("Catalog contains a repository with an invalid full_name")
    if len(set(name.casefold() for name in full_names)) != len(full_names):
        raise IndexErrorBase("Catalog contains duplicate repository names")
    if len({name.replace("/", "__").casefold() for name in full_names}) != len(full_names):
        raise IndexErrorBase("Catalog contains colliding repository storage names")

    destination = _db_path(root_path, db_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{destination.name}.", suffix=".tmp", dir=destination.parent)
    os.close(fd)
    temp_path = Path(temp_name)
    try:
        conn = sqlite3.connect(temp_path)
        try:
            _create_db(conn)
            source_count = 0
            cards_count = 0
            archived_count = 0
            for repo in repos:
                full_name = repo["full_name"]
                archived = bool(repo.get("archived", False))
                archived_count += int(archived)
                topics = repo.get("topics") if isinstance(repo.get("topics"), list) else []
                source_base = sources_dir / full_name.replace("/", "__")
                manifest = _json_file(source_base / "manifest.json") or {}
                card = _json_file(cards_dir / f"{full_name.replace('/', '__')}.json")
                if card and card.get("full_name") not in (None, full_name):
                    card = None
                cards_count += int(card is not None)
                private_value = repo.get("private")
                private_value = int(private_value) if isinstance(private_value, bool) else None
                visibility = repo.get("visibility") if isinstance(repo.get("visibility"), str) else None
                conn.execute(
                    "INSERT INTO repositories(full_name,url,description,language,topics_json,archived,private,visibility,evidence_level,repository_json) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?)",
                    (full_name, repo.get("url"), repo.get("description"), repo.get("language"),
                     json.dumps(topics, ensure_ascii=False), int(archived), private_value, visibility,
                     card.get("evidence_level", "metadata") if card else "metadata",
                     json.dumps(repo, ensure_ascii=False)),
                )
                count = _add_repo_documents(conn, root_path, repo, cards_dir, sources_dir)
                source_count += int(bool(manifest))
                conn.execute("UPDATE repositories SET doc_count=? WHERE full_name=?", (count, full_name))
            requested_count = catalog.get("count")
            catalog_count = len(repos)
            catalog_complete = bool(catalog.get("complete")) and requested_count == catalog_count
            meta = {
                "built_at": datetime.now(timezone.utc).isoformat(),
                "catalog_user": str(catalog.get("user", "")),
                "catalog_count": str(catalog_count),
                "catalog_expected_count": str(requested_count if requested_count is not None else catalog_count),
                "catalog_complete": "1" if catalog_complete else "0",
                "source_snapshots": str(source_count),
                "cards": str(cards_count),
                "archived": str(archived_count),
            }
            conn.executemany("INSERT INTO index_meta(key,value) VALUES(?,?)", meta.items())
            conn.commit()
        finally:
            conn.close()
        temp_path.replace(destination)
    except sqlite3.OperationalError as exc:
        temp_path.unlink(missing_ok=True)
        if "fts5" in str(exc).casefold() or "no such module" in str(exc).casefold():
            raise IndexErrorBase("This Python SQLite build does not include FTS5") from exc
        raise
    except Exception:
        temp_path.unlink(missing_ok=True)
        raise
    return stats(root_path, destination)


def _connect(db_path: Path) -> sqlite3.Connection:
    if not db_path.is_file():
        raise IndexErrorBase(f"Index not found: {db_path}. Run `python search.py build` first.")
    conn = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _query_excerpt(
    content: str,
    terms: list[str],
    line_offset: int | None = None,
    groups: list[dict] | None = None,
    *,
    strip_markup: bool = False,
) -> dict:
    lines = content.splitlines()
    if not lines:
        return {"excerpt": content[:_MAX_EXCERPT_CHARS], "start_line": line_offset, "end_line": line_offset}
    signal_lines = _visible_text(content).splitlines() if strip_markup else lines
    if len(signal_lines) != len(lines):
        signal_lines = lines
    match_terms = [term for term in terms if term]
    scored: list[tuple[float, int, int, int]] = []
    for index, line in enumerate(signal_lines):
        folded = line.casefold()
        token_score = sum(1 for term in match_terms if term in folded)
        coverage, group_count, _ = _group_coverage(line, groups or [])
        scored.append((coverage, group_count, token_score, index))
    if groups:
        nonempty = [(index, line) for index, line in enumerate(signal_lines) if line.strip()]
        windows = []
        for offset in range(max(1, len(nonempty) - 3)):
            selected = nonempty[offset : offset + 4]
            start_index, _ = selected[0]
            end_index, _ = selected[-1]
            window_text = "\n".join(line for _, line in selected)
            coverage, group_count, _ = _group_coverage(window_text, groups)
            windows.append((coverage, group_count, end_index - start_index, start_index, end_index))
        if windows:
            _, _, _, first, last_index = max(windows, key=lambda item: (item[0], item[1], -item[2], -item[3]))
            selected_indices = [index for index, _ in nonempty if first <= index <= last_index]
            matching_indices = [index for index in selected_indices
                                if _group_coverage(signal_lines[index], groups)[1] > 0]
            if matching_indices:
                first = matching_indices[0]
                last_index = matching_indices[-1]
            last = last_index + 1
            excerpt = "\n".join(lines[first:last]).strip()
        else:
            first = 0
            last = 1
            excerpt = lines[0].strip()
    else:
        best = max(scored, key=lambda item: (item[2], -item[3]))
        first = best[3]
        last = first + 1
        excerpt = lines[first].strip()
    best_line = first
    if not excerpt:
        excerpt = next((line.strip() for line in lines if line.strip()), "")
        first = next((i for i, line in enumerate(lines) if line.strip()), 0)
        last = first + 1
    if len(excerpt) > _MAX_EXCERPT_CHARS:
        center = best_line
        target = excerpt
        if len(target) > _MAX_EXCERPT_CHARS:
            target = target[:_MAX_EXCERPT_CHARS - 1] + "…"
        excerpt = target
    start = line_offset + first if isinstance(line_offset, int) else None
    end = line_offset + last - 1 if isinstance(line_offset, int) else None
    return {"excerpt": excerpt, "start_line": start, "end_line": end}


def _repo_row(row: sqlite3.Row) -> dict:
    license_id = None
    raw_repository = row["repository_json"] if "repository_json" in row.keys() else None
    if raw_repository:
        try:
            stored = json.loads(raw_repository)
        except (TypeError, json.JSONDecodeError):
            stored = None
        if isinstance(stored, dict):
            value = stored.get("license")
            if isinstance(value, dict):
                value = value.get("spdx_id") or value.get("name")
            if isinstance(value, str) and value.strip() and value.strip().upper() not in {"NONE", "NOASSERTION"}:
                license_id = value.strip()
    return {
        "full_name": row["full_name"],
        "url": row["url"],
        "description": row["description"],
        "language": row["language"],
        "topics": json.loads(row["topics_json"] or "[]"),
        "license": license_id,
        "archived": bool(row["archived"]),
        "private": None if row["private"] is None else bool(row["private"]),
        "visibility": row["visibility"],
        "kind": row["kind"],
        "evidence_level": row["evidence_level"],
        "card_stale": None if row["card_stale"] is None else bool(row["card_stale"]),
        "card_ready": None if row["card_ready"] is None else bool(row["card_ready"]),
        "card_source_sha": row["card_source_sha"],
        "manifest_source_sha": row["manifest_source_sha"],
    }


def _load_evidence(json_value: str | None, full_name: str, source_sha: str | None, *, excerpt_limit: int | None = None) -> list[dict]:
    if not json_value:
        return []
    try:
        values = json.loads(json_value)
    except json.JSONDecodeError:
        return []
    if not isinstance(values, list):
        return []
    result = []
    for item in values:
        if not isinstance(item, dict):
            continue
        view = _evidence_view(full_name, source_sha, item)
        if excerpt_limit and isinstance(view.get("excerpt"), str) and len(view["excerpt"]) > excerpt_limit:
            view["excerpt"] = view["excerpt"][: excerpt_limit - 1] + "…"
        result.append(view)
    return result


def search_repositories(
    query: str,
    *,
    db_path: str | Path | None = None,
    root: str | Path = ROOT,
    limit: int = 10,
    kind: str | None = None,
    language: str | None = None,
    archived: bool | None = None,
    evidence_level: str | None = None,
    extra_terms: list[str] | None = None,
    include_private: bool = True,
) -> dict:
    root_path = Path(root).resolve()
    index_path = _db_path(root_path, db_path)
    limit = max(1, min(int(limit), 100))
    terms, groups = query_plan(query, extra_terms)
    expression = _fts_expression(terms)
    conn = _connect(index_path)
    try:
        clauses = ["documents_fts MATCH ?"]
        params: list[object] = [expression]
        if not include_private:
            # Unknown visibility does not grant permission for public output.
            clauses.append("r.private = 0")
        if kind:
            clauses.append("r.kind = ?")
            params.append(kind)
        if language:
            clauses.append("lower(r.language) = lower(?)")
            params.append(language)
        if archived is not None:
            clauses.append("r.archived = ?")
            params.append(int(archived))
        if evidence_level:
            clauses.append("r.evidence_level = ?")
            params.append(evidence_level)
        sql = (
            "SELECT d.id,d.full_name,d.doc_type,d.title,d.evidence_level,d.path,d.source_sha,d.line_start,d.line_end,d.content,d.evidence_json,"
            "bm25(documents_fts,0,0,0,0,5.0,1.0) AS score "
            "FROM documents_fts JOIN documents d ON d.id=documents_fts.rowid "
            "JOIN repositories r ON r.full_name=d.full_name WHERE " + " AND ".join(clauses) +
            " ORDER BY score ASC,d.full_name COLLATE NOCASE ASC,d.id ASC LIMIT ?"
        )
        # Fetch enough matching documents to choose the best document per
        # repository without allowing a single repository's long README to
        # crowd every other star out of the result set.
        params.append(min(max(limit * 30, 300), 5000))
        matches = conn.execute(sql, params).fetchall()
        # Task/use-case and feature claims are the best recommendation signals;
        # evidence excerpts support those claims but often contain incidental
        # words from implementation notes.
        doc_priority = {"feature": 6, "card": 5, "source_doc": 2, "evidence": 1.5, "metadata": 1, "card_detail": 0.5}
        ranked: dict[str, list[tuple[tuple, sqlite3.Row, tuple[float, int, int]]]] = {}
        for row in matches:
            joined = row["content"] if row["doc_type"] == "source_doc" else row["title"] + "\n" + row["content"]
            signal_text = _visible_text(joined) if row["doc_type"] == "source_doc" else joined
            coverage = _group_coverage(signal_text, groups, window_lines=4 if row["doc_type"] == "source_doc" else None)
            ratio, matched_count, total_groups = coverage
            # If the query has understood concepts, discard documents that only
            # matched stray bigrams or boilerplate and none of those concepts.
            if groups and matched_count == 0:
                continue
            priority = doc_priority.get(row["doc_type"], 0)
            sort_key = (-ratio, -matched_count, -priority, row["score"], row["full_name"].casefold(), row["id"])
            ranked.setdefault(row["full_name"], []).append((sort_key, row, coverage))
        for repo_rows in ranked.values():
            repo_rows.sort(key=lambda item: item[0])
        repo_order = sorted(ranked, key=lambda name: ranked[name][0][0])
        result = []
        for full_name in repo_order:
            ranked_rows = ranked[full_name]
            primary = ranked_rows[0][1]
            rows = [item[1] for item in ranked_rows]
            primary_coverage = ranked_rows[0][2]
            repo_row = conn.execute("SELECT * FROM repositories WHERE full_name=?", (full_name,)).fetchone()
            if repo_row is None:
                continue
            meta = _repo_row(repo_row)
            snippet = _query_excerpt(primary["content"], terms, primary["line_start"], groups,
                                     strip_markup=primary["doc_type"] == "source_doc")
            match_out = {
                "type": primary["doc_type"],
                "title": primary["title"],
                "evidence_level": primary["evidence_level"],
                **snippet,
                "path": primary["path"],
                "source_sha": primary["source_sha"],
                "url": evidence_url(full_name, primary["source_sha"], primary["path"], snippet["start_line"], snippet["end_line"])
                       if primary["path"] else None,
            }
            evidence: list[dict] = []
            seen_evidence: set[tuple] = set()
            for row in rows:
                for item in _load_evidence(row["evidence_json"], full_name, row["source_sha"], excerpt_limit=240):
                    key = (item.get("id"), item.get("path"), item.get("start_line"), item.get("end_line"))
                    if key not in seen_evidence:
                        item["match_context"] = "matched document"
                        seen_evidence.add(key)
                        evidence.append(item)
                    if len(evidence) >= 3:
                        break
                if len(evidence) >= 3:
                    break
            # Attribute features by their own document hit. A feature counts
            # only when the query concepts matched that feature's text, so
            # unrelated capabilities are not reported just because the
            # repository's card or README also surfaced.
            feature_matches: list[dict] = []
            for sort_key, row, coverage in ranked_rows:
                if row["doc_type"] != "feature" or coverage[1] < 1:
                    continue
                feature_matches.append({
                    "name": row["title"],
                    "evidence_level": row["evidence_level"],
                    "evidence": _load_evidence(row["evidence_json"], full_name, row["source_sha"], excerpt_limit=240),
                })
                if len(feature_matches) >= 5:
                    break
            # The matched card summary may not itself carry evidence IDs. In
            # that case, expose a few of the repository's inspected source
            # excerpts as context, clearly distinguished from direct matches.
            if len(evidence) < 3:
                related = conn.execute(
                    "SELECT evidence_json,source_sha FROM documents WHERE full_name=? AND doc_type='evidence' ORDER BY id LIMIT ?",
                    (full_name, 3 - len(evidence)),
                ).fetchall()
                for row in related:
                    for item in _load_evidence(row["evidence_json"], full_name, row["source_sha"], excerpt_limit=240):
                        key = (item.get("id"), item.get("path"), item.get("start_line"), item.get("end_line"))
                        if key not in seen_evidence:
                            item["match_context"] = "repository card evidence"
                            seen_evidence.add(key)
                            evidence.append(item)
            ratio, matched_groups, total_groups = primary_coverage
            matched_labels, missing_labels = _coverage_labels(
                _visible_text(primary["content"]) if primary["doc_type"] == "source_doc" else primary["title"] + "\n" + primary["content"], groups,
                window_lines=4 if primary["doc_type"] == "source_doc" else None,
            )
            result.append({**meta, "lexical_coverage": {"matched_groups": matched_groups, "total_groups": total_groups,
                                                            "ratio": round(ratio, 3), "matched": matched_labels,
                                                            "missing": missing_labels},
                           "match": match_out, "evidence": evidence,
                           "feature_matches": feature_matches})
            if len(result) >= limit:
                break
        return {"query": query, "query_terms": terms, "count": len(result), "results": result}
    except sqlite3.OperationalError as exc:
        if "no such table" in str(exc).casefold() or "no such column" in str(exc).casefold():
            raise IndexErrorBase(f"Index schema is invalid: {index_path}. Rebuild it with `python search.py build`.") from exc
        raise
    finally:
        conn.close()


def show_repo(full_name: str, *, db_path: str | Path | None = None, root: str | Path = ROOT) -> dict:
    root_path = Path(root).resolve()
    conn = _connect(_db_path(root_path, db_path))
    try:
        row = conn.execute("SELECT * FROM repositories WHERE lower(full_name)=lower(?)", (full_name,)).fetchone()
        if row is None:
            raise IndexErrorBase(f"Repository not found in index: {full_name}")
        item = _repo_row(row)
        raw_card = json.loads(row["card_json"]) if row["card_json"] else None
        card_status = "missing" if raw_card is None else "current"
        if raw_card is not None:
            if item["card_stale"] is True:
                card_status = "stale"
            elif item["card_stale"] is None:
                card_status = "freshness_unknown"
            elif item["card_ready"] is not True:
                card_status = "unfinalized"
        if isinstance(raw_card, dict):
            source_sha = raw_card.get("source_sha")
            evidence = raw_card.get("evidence") if isinstance(raw_card.get("evidence"), list) else []
            raw_card["evidence"] = [
                {**ev, "url": evidence_url(row["full_name"], source_sha, ev.get("path"), ev.get("start_line"), ev.get("end_line")),
                 "path_safe": safe_repo_path(ev.get("path")) if isinstance(ev.get("path"), str) else False}
                for ev in evidence if isinstance(ev, dict)
            ]
        docs = conn.execute(
            "SELECT doc_type,COUNT(*) AS count FROM documents WHERE full_name=? GROUP BY doc_type ORDER BY doc_type",
            (row["full_name"],),
        ).fetchall()
        return {"repository": item, "card_status": card_status, "card": raw_card,
                "indexed_documents": {doc["doc_type"]: doc["count"] for doc in docs}}
    finally:
        conn.close()


def stats(root: str | Path = ROOT, db_path: str | Path | None = None) -> dict:
    root_path = Path(root).resolve()
    conn = _connect(_db_path(root_path, db_path))
    try:
        meta = {row["key"]: row["value"] for row in conn.execute("SELECT key,value FROM index_meta")}
        levels = {row["evidence_level"]: row["count"] for row in conn.execute(
            "SELECT evidence_level,COUNT(*) AS count FROM repositories GROUP BY evidence_level ORDER BY evidence_level")}
        kinds = {row["kind"] or "unclassified": row["count"] for row in conn.execute(
            "SELECT kind,COUNT(*) AS count FROM repositories GROUP BY kind ORDER BY kind")}
        doc_types = {row["doc_type"]: row["count"] for row in conn.execute(
            "SELECT doc_type,COUNT(*) AS count FROM documents GROUP BY doc_type ORDER BY doc_type")}
        stale = conn.execute("SELECT COUNT(*) FROM repositories WHERE card_stale=1").fetchone()[0]
        current = conn.execute("SELECT COUNT(*) FROM repositories WHERE card_stale=0").fetchone()[0]
        unknown = conn.execute("SELECT COUNT(*) FROM repositories WHERE card_stale IS NULL").fetchone()[0]
        ready = conn.execute("SELECT COUNT(*) FROM repositories WHERE card_ready=1").fetchone()[0]
        unfinalized = conn.execute("SELECT COUNT(*) FROM repositories WHERE card_stale=0 AND card_ready=0").fetchone()[0]
        return {
            "built_at": meta.get("built_at"),
            "catalog_user": meta.get("catalog_user"),
            "catalog_count": int(meta.get("catalog_count", 0)),
            "catalog_expected_count": int(meta.get("catalog_expected_count", 0)),
            "catalog_complete": meta.get("catalog_complete") == "1",
            "source_snapshots": int(meta.get("source_snapshots", 0)),
            "cards": int(meta.get("cards", 0)),
            "repositories": conn.execute("SELECT COUNT(*) FROM repositories").fetchone()[0],
            "archived": conn.execute("SELECT COUNT(*) FROM repositories WHERE archived=1").fetchone()[0],
            "private": conn.execute("SELECT COUNT(*) FROM repositories WHERE private=1").fetchone()[0],
            "stale_cards": stale,
            "current_cards": current,
            "ready_cards": ready,
            "unfinalized_cards": unfinalized,
            "card_freshness_unknown": unknown,
            "evidence_levels": levels,
            "kinds": kinds,
            "indexed_documents": doc_types,
        }
    finally:
        conn.close()


def _format_search(result: dict) -> str:
    lines = [f"找到 {result['count']} 个候选（查询词：{' '.join(result['query_terms'])}）"]
    for index, item in enumerate(result["results"], 1):
        labels = [item.get("kind") or "未分类", item.get("language") or "语言未知", f"证据={item.get('evidence_level')}"]
        if item.get("visibility"):
            labels.append(item["visibility"])
        if item.get("private"):
            labels.append("private")
        if item.get("archived"):
            labels.append("已归档")
        if item.get("card_stale"):
            labels.append("卡片对应旧版本")
        elif item.get("card_ready") is False:
            labels.append("分析卡未完成")
        lines.append(f"\n{index}. {item['full_name']} — {' · '.join(labels)}")
        if item.get("description"):
            lines.append(f"   {item['description']}")
        match = item["match"]
        lines.append(f"   匹配：{match['title']}（{match['type']}）")
        coverage = item.get("lexical_coverage", {})
        if coverage.get("total_groups"):
            lines.append(f"   词法概念覆盖：{coverage['matched_groups']}/{coverage['total_groups']}（词项匹配，不是语义向量分数）")
            if coverage.get("missing"):
                lines.append(f"   未命中需求词：{', '.join(coverage['missing'])}")
        if match.get("excerpt"):
            line = f"   摘录：{match['excerpt'].replace(chr(10), ' / ')}"
            if match.get("start_line") is not None:
                line += f"  [{match['start_line']}-{match['end_line']}]"
            lines.append(line)
        if match.get("url"):
            lines.append(f"   来源：{match['url']}")
        for evidence in item.get("evidence", []):
            lines.append(f"   证据：{evidence.get('claim') or evidence.get('path')} — {evidence.get('url') or '路径或 commit 不安全，未生成链接'}")
    return "\n".join(lines)


def _add_db_argument(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--db", help="SQLite index path (default: schema.json database)")
    parser.add_argument("--root", type=Path, default=ROOT, help="Index data root containing schema.json")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Search your starred-repository index.")
    sub = parser.add_subparsers(dest="command", required=True)
    build_parser = sub.add_parser("build", help="atomically rebuild the local index")
    _add_db_argument(build_parser)
    build_parser.add_argument("--json", action="store_true")

    search_parser = sub.add_parser("search", help="search repository purpose and evidence")
    search_parser.add_argument("query", nargs="+", help="natural-language query")
    search_parser.add_argument("--kind")
    search_parser.add_argument("--language")
    search_parser.add_argument("--archived", choices=("true", "false", "any"), default="any")
    search_parser.add_argument("--evidence-level", choices=("metadata", "readme", "docs", "source"))
    search_parser.add_argument("--limit", type=int, default=10)
    search_parser.add_argument("--terms", action="append", nargs="+", default=[], help="extra search terms; repeatable")
    search_parser.add_argument("--json", action="store_true")
    search_parser.add_argument("--public-only", action="store_true", help="Exclude private and unknown-visibility repositories")
    _add_db_argument(search_parser)

    show_parser = sub.add_parser("show", help="show a repository's complete card and evidence")
    show_parser.add_argument("repository", help="owner/repository")
    show_parser.add_argument("--json", action="store_true")
    _add_db_argument(show_parser)

    stats_parser = sub.add_parser("stats", help="show index coverage and freshness")
    stats_parser.add_argument("--json", action="store_true")
    _add_db_argument(stats_parser)

    options = parser.parse_args(argv)
    try:
        if options.command == "build":
            result = build_index(root=options.root, db_path=options.db)
            print(json.dumps(result, ensure_ascii=False, indent=2) if options.json else json.dumps(result, ensure_ascii=False))
        elif options.command == "search":
            archived = None if options.archived == "any" else options.archived == "true"
            extras = [term for group in options.terms for term in group]
            result = search_repositories(" ".join(options.query), root=options.root, db_path=options.db, limit=options.limit,
                                         kind=options.kind, language=options.language, archived=archived,
                                         evidence_level=options.evidence_level, extra_terms=extras,
                                         include_private=not options.public_only)
            # feature_matches is the service-side attribution input; the public
            # search surface does not repeat it.
            for item in result.get("results", []):
                item.pop("feature_matches", None)
            print(json.dumps(result, ensure_ascii=False, indent=2) if options.json else _format_search(result))
        elif options.command == "show":
            result = show_repo(options.repository, root=options.root, db_path=options.db)
            print(json.dumps(result, ensure_ascii=False, indent=2) if options.json else json.dumps(result, ensure_ascii=False, indent=2))
        else:
            result = stats(root=options.root, db_path=options.db)
            print(json.dumps(result, ensure_ascii=False, indent=2) if options.json else json.dumps(result, ensure_ascii=False, indent=2))
    except (IndexErrorBase, ValueError, sqlite3.Error) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
