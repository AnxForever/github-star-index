"""Tests for the optional embedding layer.

These use a deterministic stub embedder and never touch the network, so the
semantic path is verified without a provider key. A real key is only needed for
an actual end-to-end embedding build.
"""
from __future__ import annotations

import json
import math
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import embeddings
import search


def _vec(text: str, dims: int = 32) -> list[float]:
    """Deterministic pseudo-embedding: bag-of-words hashed into fixed dims."""
    vector = [0.0] * dims
    for token in text.casefold().split():
        vector[hash(token) % dims] += 1.0
    magnitude = math.sqrt(sum(value * value for value in vector)) or 1.0
    return [value / magnitude for value in vector]


class EmbeddingsUnitTests(unittest.TestCase):
    def test_pack_and_unpack_round_trip_is_normalized(self) -> None:
        blob = embeddings.pack_vector([3.0, 4.0])
        self.assertEqual(embeddings.dimensions_of(blob), 2)
        values = embeddings.unpack_vector(blob)
        self.assertAlmostEqual(values[0], 0.6, places=5)
        self.assertAlmostEqual(values[1], 0.8, places=5)

    def test_rank_by_similarity_orders_and_reports_dimension_mismatch(self) -> None:
        vectors = [embeddings.pack_vector(_vec("react animation")),
                   embeddings.pack_vector(_vec("database query"))]
        # Query identical to the first text should rank it first.
        ranked = embeddings.rank_by_similarity(_vec("react animation"), vectors)
        self.assertEqual(ranked[0][0], 0)
        with self.assertRaises(embeddings.EmbeddingError):
            embeddings.rank_by_similarity(_vec("react animation", dims=8), vectors)

    def test_config_is_none_without_key_and_never_exposes_secret(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            self.assertIsNone(embeddings.config(None))
        with patch.dict(os.environ, {"GSI_EMBEDDING_API_KEY": "secret-value"}, clear=True):
            cfg = embeddings.config(None)
        self.assertIsNotNone(cfg)
        self.assertNotIn("secret-value", json.dumps(cfg.public()))
        self.assertTrue(cfg.public()["has_api_key"])


class SemanticSearchTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root / "data" / "cards").mkdir(parents=True)
        (self.root / "data" / "sources").mkdir(parents=True)
        (self.root / "schema.json").write_text(json.dumps({"database": "data/index.sqlite3"}), encoding="utf-8")
        repositories = [
            {"full_name": "acme/motion-kit", "url": "https://github.com/acme/motion-kit",
             "description": "Smooth animation components", "language": "TypeScript", "license": "MIT"},
            {"full_name": "acme/sql-store", "url": "https://github.com/acme/sql-store",
             "description": "Relational storage engine", "language": "Go", "license": "MIT"},
        ]
        (self.root / "data" / "catalog.json").write_text(
            json.dumps({"user": "tester", "count": 2, "complete": True, "repositories": repositories}),
            encoding="utf-8",
        )
        self.db = self.root / "data" / "index.sqlite3"
        search.build_index(self.root)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _stub_embed(self, texts, *, cfg=None, root=None):
        return [_vec(text) for text in texts]

    def _fake_config(self, model: str = "stub-model"):
        return embeddings.EmbeddingConfig(base_url="http://localhost", model=model, api_key="test",
                                          dimensions=None, batch=16)

    def test_embed_build_populates_vectors_without_network(self) -> None:
        before = search.embedding_status(self.root)
        self.assertFalse(before["available"])
        with patch.object(embeddings, "embed", side_effect=self._stub_embed):
            result = search.build_embeddings(self.root, cfg=self._fake_config())
        self.assertGreater(result["embedded"], 0)
        after = search.embedding_status(self.root, db_path=self.db)
        self.assertTrue(after["available"])
        self.assertEqual(after["models"], ["stub-model"])
        self.assertEqual(after["dims"], 32)

    def test_search_without_provider_is_unchanged_and_lexical_only(self) -> None:
        result = search.search_repositories("relational storage", root=self.root)
        self.assertEqual(result["retrieval"]["semantic"], False)
        self.assertEqual(result["retrieval"]["semantic_candidates"], 0)
        self.assertTrue(all(item["semantic_similarity"] is None for item in result["results"]))

    def test_semantic_fusion_engages_only_when_configured(self) -> None:
        with patch.object(embeddings, "embed", side_effect=self._stub_embed):
            search.build_embeddings(self.root, cfg=self._fake_config())
            with patch.object(embeddings, "config", return_value=self._fake_config()):
                fused = search.search_repositories("smooth animation", root=self.root)
            off = search.search_repositories("smooth animation", root=self.root, semantic=False)
        self.assertTrue(fused["retrieval"]["semantic"])
        self.assertGreater(fused["retrieval"]["semantic_candidates"], 0)
        self.assertFalse(off["retrieval"]["semantic"])
        self.assertEqual(off["retrieval"]["semantic_candidates"], 0)
        # A fused hit carries a similarity; an explicitly lexical-only one does not.
        self.assertTrue(any(item["semantic_similarity"] is not None for item in fused["results"]))
        self.assertTrue(all(item["semantic_similarity"] is None for item in off["results"]))

    def test_model_mismatch_disables_fusion_instead_of_comparing_vectors(self) -> None:
        with patch.object(embeddings, "embed", side_effect=self._stub_embed):
            search.build_embeddings(self.root, cfg=self._fake_config("model-a"))
            # Index holds model-a vectors; a different configured model must not
            # be silently compared against them.
            with patch.object(embeddings, "config", return_value=self._fake_config("model-b")):
                result = search.search_repositories("smooth animation", root=self.root)
        self.assertFalse(result["retrieval"]["semantic"])

    def test_embed_requires_a_provider(self) -> None:
        with patch.object(embeddings, "config", return_value=None):
            with self.assertRaises(search.IndexErrorBase):
                search.build_embeddings(self.root)


if __name__ == "__main__":
    unittest.main()
