"""Tests for the content-addressed blob store."""

import os
import tempfile
from pathlib import Path

import pytest

from intercache.store import BlobStore


@pytest.fixture
def tmp_cache(tmp_path):
    return BlobStore(root=tmp_path)


class TestBlobStore:
    def test_store_and_lookup(self, tmp_cache):
        content = b"hello world"
        sha256 = tmp_cache.store(content)
        assert len(sha256) == 64
        assert tmp_cache.lookup(sha256) == content

    def test_deduplication(self, tmp_cache):
        content = b"same content twice"
        sha1 = tmp_cache.store(content)
        sha2 = tmp_cache.store(content)
        assert sha1 == sha2

    def test_lookup_missing(self, tmp_cache):
        assert tmp_cache.lookup("a" * 64) is None

    def test_delete(self, tmp_cache):
        sha256 = tmp_cache.store(b"to delete")
        assert tmp_cache.delete(sha256) is True
        assert tmp_cache.lookup(sha256) is None
        assert tmp_cache.delete(sha256) is False

    def test_stats(self, tmp_cache):
        tmp_cache.store(b"file one")
        tmp_cache.store(b"file two")
        stats = tmp_cache.stats()
        assert stats["blob_count"] == 2
        assert stats["total_bytes"] > 0

    def test_purge(self, tmp_cache):
        tmp_cache.store(b"a")
        tmp_cache.store(b"b")
        count = tmp_cache.purge()
        assert count == 2
        assert tmp_cache.stats()["blob_count"] == 0

    def test_hash_content_deterministic(self, tmp_cache):
        h1 = BlobStore.hash_content(b"deterministic")
        h2 = BlobStore.hash_content(b"deterministic")
        assert h1 == h2

    def test_different_content_different_hash(self, tmp_cache):
        h1 = BlobStore.hash_content(b"alpha")
        h2 = BlobStore.hash_content(b"beta")
        assert h1 != h2

    def test_shard_directory_structure(self, tmp_cache):
        sha256 = tmp_cache.store(b"sharded content")
        blob_path = tmp_cache.root / sha256[:2] / sha256[2:]
        assert blob_path.exists()
