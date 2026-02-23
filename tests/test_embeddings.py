"""Tests for embedding storage layer.

Tests the SQLite storage, model versioning, and query logic using mock vectors.
Does NOT require sentence-transformers — injects a mock embedder.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import numpy as np
import pytest

from intercache.embeddings import EmbeddingStore, vector_to_bytes, bytes_to_vector


@pytest.fixture
def emb_store(tmp_path):
    store = EmbeddingStore("test_project", cache_dir=tmp_path)
    yield store
    store.close()


class MockEmbedder:
    """Deterministic mock embedder for testing without sentence-transformers."""

    def embed(self, text: str) -> np.ndarray:
        # Generate a deterministic vector from the text hash
        rng = np.random.RandomState(hash(text) % 2**31)
        vec = rng.randn(768).astype(np.float32)
        vec /= np.linalg.norm(vec)
        return vec

    def embed_batch(self, texts: list[str]) -> np.ndarray:
        return np.array([self.embed(t) for t in texts])

    def cosine_similarity(self, a: np.ndarray, b: np.ndarray) -> float:
        return float(np.dot(a, b))


class TestVectorSerialization:
    def test_roundtrip(self):
        vec = np.random.randn(768).astype(np.float32)
        data = vector_to_bytes(vec)
        result = bytes_to_vector(data)
        np.testing.assert_array_almost_equal(vec, result)

    def test_correct_dtype(self):
        vec = np.array([1.0, 2.0, 3.0], dtype=np.float32)
        data = vector_to_bytes(vec)
        assert len(data) == 12  # 3 * 4 bytes


class TestEmbeddingStore:
    def test_index_and_query(self, emb_store):
        emb_store._embedder = MockEmbedder()

        # Index some files
        assert emb_store.index_file("src/main.py", "print('hello world')", "abc123") is True
        assert emb_store.index_file("src/util.py", "def add(a, b): return a + b", "def456") is True
        assert emb_store.count() == 2

        # Query
        results = emb_store.query("hello world")
        assert len(results) == 2
        assert results[0]["path"] in ("src/main.py", "src/util.py")
        assert results[0]["score"] >= results[1]["score"]  # Sorted by score

    def test_skip_already_indexed(self, emb_store):
        emb_store._embedder = MockEmbedder()

        assert emb_store.index_file("a.py", "content", "hash1") is True
        assert emb_store.index_file("a.py", "content", "hash1") is False  # Same hash → skip

    def test_reindex_on_hash_change(self, emb_store):
        emb_store._embedder = MockEmbedder()

        emb_store.index_file("a.py", "old content", "hash1")
        assert emb_store.index_file("a.py", "new content", "hash2") is True
        assert emb_store.count() == 1  # Still just one entry

    def test_invalidate(self, emb_store):
        emb_store._embedder = MockEmbedder()

        emb_store.index_file("a.py", "content", "h1")
        assert emb_store.invalidate("a.py") is True
        assert emb_store.invalidate("a.py") is False
        assert emb_store.count() == 0

    def test_empty_query(self, emb_store):
        emb_store._embedder = MockEmbedder()
        results = emb_store.query("anything")
        assert results == []

    def test_stale_paths(self, emb_store):
        emb_store._embedder = MockEmbedder()

        emb_store.index_file("a.py", "content a", "hash_a")
        emb_store.index_file("b.py", "content b", "hash_b")

        manifest_entries = [
            {"path": "a.py", "sha256": "hash_a"},  # Up to date
            {"path": "b.py", "sha256": "hash_b_new"},  # Changed
            {"path": "c.py", "sha256": "hash_c"},  # Not indexed yet
        ]

        stale = emb_store.stale_paths(manifest_entries)
        assert "b.py" in stale  # Hash mismatch
        assert "c.py" in stale  # Not indexed
        assert "a.py" not in stale  # Up to date
