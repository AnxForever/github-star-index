"""Optional semantic embeddings for the repository index.

The lexical FTS5 path is the default and needs no network or credentials. This
module adds an optional embedding layer used for hybrid retrieval, so spoken or
paraphrased tasks that share few literal words with a repository can still find
it.

Design boundaries:

- No new Python dependencies. Requests use ``urllib``; vector math prefers
  ``numpy`` when present and falls back to pure Python.
- Key material is read from environment variables or a local gitignored file
  (:data:`CONFIG_FILE`), never from the repository, and is never logged.
- Nothing here runs unless a provider is configured. An index without
  embeddings keeps working exactly as before.

Configuration precedence: environment variables, then the local config file.

    GSI_EMBEDDING_API_KEY      required secret
    GSI_EMBEDDING_BASE_URL     default https://api.openai.com/v1
    GSI_EMBEDDING_MODEL        default text-embedding-3-small
    GSI_EMBEDDING_DIMENSIONS   optional; sent when the provider supports it
    GSI_EMBEDDING_BATCH        default 96
"""
from __future__ import annotations

import array
import json
import math
import os
import struct
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

CONFIG_FILE_NAME = "embeddings.json"
DEFAULT_BASE_URL = "https://api.openai.com/v1"
DEFAULT_MODEL = "text-embedding-3-small"
DEFAULT_BATCH = 96
REQUEST_TIMEOUT = 60
MAX_RETRIES = 3


class EmbeddingError(RuntimeError):
    """Expected embedding configuration or transport error."""


@dataclass(frozen=True)
class EmbeddingConfig:
    base_url: str
    model: str
    api_key: str
    dimensions: int | None
    batch: int

    def public(self) -> dict:
        """Describe the configuration without exposing the secret."""
        return {
            "base_url": self.base_url,
            "model": self.model,
            "dimensions": self.dimensions,
            "has_api_key": bool(self.api_key),
        }


def _read_config_file(root: Path | None) -> dict:
    if root is None:
        return {}
    path = Path(root) / "data" / CONFIG_FILE_NAME
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def config(root: str | Path | None = None) -> EmbeddingConfig | None:
    """Return the active embedding configuration, or None when unconfigured."""
    file_values = _read_config_file(Path(root) if root is not None else None)

    def pick(env: str, key: str, default: str | None = None) -> str | None:
        value = os.environ.get(env)
        if value is None or not str(value).strip():
            value = file_values.get(key)
        if value is None:
            return default
        text = str(value).strip()
        return text or default

    api_key = pick("GSI_EMBEDDING_API_KEY", "api_key")
    if not api_key:
        return None
    base_url = (pick("GSI_EMBEDDING_BASE_URL", "base_url", DEFAULT_BASE_URL) or DEFAULT_BASE_URL).rstrip("/")
    model = pick("GSI_EMBEDDING_MODEL", "model", DEFAULT_MODEL) or DEFAULT_MODEL

    dimensions: int | None = None
    raw_dimensions = pick("GSI_EMBEDDING_DIMENSIONS", "dimensions")
    if raw_dimensions:
        try:
            dimensions = int(raw_dimensions)
        except ValueError as exc:
            raise EmbeddingError("GSI_EMBEDDING_DIMENSIONS must be an integer") from exc
        if dimensions <= 0:
            raise EmbeddingError("GSI_EMBEDDING_DIMENSIONS must be positive")

    batch = DEFAULT_BATCH
    raw_batch = pick("GSI_EMBEDDING_BATCH", "batch")
    if raw_batch:
        try:
            batch = int(raw_batch)
        except ValueError as exc:
            raise EmbeddingError("GSI_EMBEDDING_BATCH must be an integer") from exc
        if not 1 <= batch <= 2048:
            raise EmbeddingError("GSI_EMBEDDING_BATCH must be between 1 and 2048")

    return EmbeddingConfig(base_url=base_url, model=model, api_key=api_key, dimensions=dimensions, batch=batch)


def is_configured(root: str | Path | None = None) -> bool:
    try:
        return config(root) is not None
    except EmbeddingError:
        return False


def _post(cfg: EmbeddingConfig, payload: dict) -> dict:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        f"{cfg.base_url}/embeddings",
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {cfg.api_key}",
            "Accept": "application/json",
        },
    )
    last_error: Exception | None = None
    for attempt in range(MAX_RETRIES):
        try:
            with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            # 4xx other than 429 are not worth retrying.
            detail = ""
            try:
                detail = exc.read().decode("utf-8", "replace")[:300]
            except Exception:  # noqa: BLE001 - best-effort redacted detail
                detail = ""
            last_error = EmbeddingError(f"Embedding provider returned HTTP {exc.code}: {detail}")
            if exc.code != 429 and exc.code < 500:
                break
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            last_error = EmbeddingError(f"Embedding request failed: {type(exc).__name__}")
        time.sleep(2 ** attempt)
    raise last_error or EmbeddingError("Embedding request failed")


def embed(texts: list[str], *, cfg: EmbeddingConfig | None = None, root: str | Path | None = None) -> list[list[float]]:
    """Embed a list of texts, preserving input order."""
    if not texts:
        return []
    active = cfg or config(root)
    if active is None:
        raise EmbeddingError("No embedding provider is configured")
    output: list[list[float]] = []
    for start in range(0, len(texts), active.batch):
        chunk = texts[start:start + active.batch]
        payload: dict = {"model": active.model, "input": chunk}
        if active.dimensions is not None:
            payload["dimensions"] = active.dimensions
        response = _post(active, payload)
        data = response.get("data")
        if not isinstance(data, list) or len(data) != len(chunk):
            raise EmbeddingError("Embedding provider returned an unexpected payload shape")
        ordered = sorted(data, key=lambda item: item.get("index", 0) if isinstance(item, dict) else 0)
        for item in ordered:
            vector = item.get("embedding") if isinstance(item, dict) else None
            if not isinstance(vector, list) or not vector:
                raise EmbeddingError("Embedding provider returned a missing vector")
            try:
                output.append([float(value) for value in vector])
            except (TypeError, ValueError) as exc:
                raise EmbeddingError("Embedding provider returned a non-numeric vector") from exc
    return output


def pack_vector(vector: list[float]) -> bytes:
    """Serialize a normalized float32 vector for storage."""
    normalized = normalize(vector)
    return array.array("f", normalized).tobytes()


def unpack_vector(blob: bytes) -> list[float]:
    values = array.array("f")
    values.frombytes(blob)
    return list(values)


def normalize(vector: list[float]) -> list[float]:
    magnitude = math.sqrt(sum(value * value for value in vector))
    if magnitude == 0:
        return list(vector)
    return [value / magnitude for value in vector]


def dimensions_of(blob: bytes) -> int:
    if len(blob) % 4:
        return 0
    return len(blob) // 4


def _numpy():
    try:
        import numpy  # noqa: PLC0415 - optional acceleration only
    except Exception:  # noqa: BLE001 - any import failure falls back to pure Python
        return None
    return numpy


def rank_by_similarity(query: list[float], vectors: list[bytes]) -> list[tuple[int, float]]:
    """Return (position, cosine) pairs sorted by descending similarity.

    Vectors are stored normalized, so cosine similarity is a dot product.
    """
    if not vectors:
        return []
    query_norm = normalize(query)
    numpy = _numpy()
    if numpy is not None:
        matrix = numpy.frombuffer(b"".join(vectors), dtype=numpy.float32).reshape(len(vectors), -1)
        if matrix.shape[1] != len(query_norm):
            raise EmbeddingError(
                f"Query embedding has {len(query_norm)} dimensions but the index has {matrix.shape[1]}"
            )
        scores = matrix @ numpy.asarray(query_norm, dtype=numpy.float32)
        order = numpy.argsort(-scores, kind="stable")
        return [(int(index), float(scores[index])) for index in order]
    results: list[tuple[int, float]] = []
    for index, blob in enumerate(vectors):
        vector = unpack_vector(blob)
        if len(vector) != len(query_norm):
            raise EmbeddingError(
                f"Query embedding has {len(query_norm)} dimensions but the index has {len(vector)}"
            )
        results.append((index, sum(a * b for a, b in zip(query_norm, vector))))
    results.sort(key=lambda item: (-item[1], item[0]))
    return results


def vector_size_matches(blob: bytes, dimensions: int) -> bool:
    return dimensions > 0 and struct.calcsize("f") * dimensions == len(blob)
