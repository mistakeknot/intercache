"""Integration tests for MCP server tool handlers.

Tests the full pipeline: tool handler → store/manifest/session → verify results.
Does NOT require a running MCP server — calls handlers directly.
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from unittest.mock import patch

import pytest

from intercache import server as srv


@pytest.fixture
def tmp_project(tmp_path):
    """Create a temp project with files."""
    proj = tmp_path / "project"
    proj.mkdir()
    (proj / "src").mkdir()
    (proj / "src" / "main.py").write_text("print('hello')")
    (proj / "src" / "util.py").write_text("def add(a, b): return a + b")
    (proj / "README.md").write_text("# My Project")
    return str(proj)


@pytest.fixture
def tmp_cache(tmp_path):
    """Point all caches at a temp directory."""
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    with patch.object(srv, "_blob_store", None), \
         patch.object(srv, "_manifests", {}), \
         patch.object(srv, "_sessions", {}), \
         patch.object(srv, "_embeddings", {}):
        # Patch DEFAULT_CACHE_DIR in all modules
        with patch("intercache.store.DEFAULT_CACHE_DIR", cache_dir), \
             patch("intercache.manifest.DEFAULT_CACHE_DIR", cache_dir), \
             patch("intercache.session.DEFAULT_CACHE_DIR", cache_dir), \
             patch("intercache.embeddings.DEFAULT_CACHE_DIR", cache_dir):
            yield cache_dir


def _parse(result) -> dict:
    """Extract JSON from TextContent list."""
    return json.loads(result[0].text)


class TestCacheStoreAndLookup:
    def test_store_then_lookup(self, tmp_project, tmp_cache):
        # Store a file
        result = asyncio.run(srv._handle_cache_store({
            "path": "src/main.py",
            "project_root": tmp_project,
        }))
        data = _parse(result)
        assert data["stored"] is True
        assert data["sha256"]
        assert data["size"] > 0

        # Look it up
        result = asyncio.run(srv._handle_cache_lookup({
            "path": "src/main.py",
            "project_root": tmp_project,
        }))
        data = _parse(result)
        assert data["hit"] is True
        assert "hello" in data["content"]

    def test_lookup_miss(self, tmp_project, tmp_cache):
        result = asyncio.run(srv._handle_cache_lookup({
            "path": "nonexistent.py",
            "project_root": tmp_project,
        }))
        data = _parse(result)
        assert data["hit"] is False

    def test_store_nonexistent_file(self, tmp_project, tmp_cache):
        result = asyncio.run(srv._handle_cache_store({
            "path": "does_not_exist.py",
            "project_root": tmp_project,
        }))
        data = _parse(result)
        assert "error" in data

    def test_lookup_after_file_change(self, tmp_project, tmp_cache):
        # Store original
        asyncio.run(srv._handle_cache_store({
            "path": "src/main.py",
            "project_root": tmp_project,
        }))

        # Modify the file
        with open(os.path.join(tmp_project, "src/main.py"), "w") as f:
            f.write("print('changed')")

        # Lookup should detect the change
        result = asyncio.run(srv._handle_cache_lookup({
            "path": "src/main.py",
            "project_root": tmp_project,
        }))
        data = _parse(result)
        assert data["hit"] is False


class TestCacheInvalidate:
    def test_invalidate_specific_paths(self, tmp_project, tmp_cache):
        asyncio.run(srv._handle_cache_store({"path": "src/main.py", "project_root": tmp_project}))
        asyncio.run(srv._handle_cache_store({"path": "src/util.py", "project_root": tmp_project}))

        result = asyncio.run(srv._handle_cache_invalidate({
            "paths": ["src/main.py"],
            "project_root": tmp_project,
        }))
        data = _parse(result)
        assert data["invalidated"] == 1

        # main.py should miss now
        result = asyncio.run(srv._handle_cache_lookup({"path": "src/main.py", "project_root": tmp_project}))
        assert _parse(result)["hit"] is False

        # util.py should still hit
        result = asyncio.run(srv._handle_cache_lookup({"path": "src/util.py", "project_root": tmp_project}))
        assert _parse(result)["hit"] is True

    def test_invalidate_by_pattern(self, tmp_project, tmp_cache):
        asyncio.run(srv._handle_cache_store({"path": "src/main.py", "project_root": tmp_project}))
        asyncio.run(srv._handle_cache_store({"path": "src/util.py", "project_root": tmp_project}))
        asyncio.run(srv._handle_cache_store({"path": "README.md", "project_root": tmp_project}))

        result = asyncio.run(srv._handle_cache_invalidate({
            "pattern": "src/%",
            "project_root": tmp_project,
        }))
        data = _parse(result)
        assert data["invalidated"] == 2


class TestCacheStats:
    def test_stats_empty(self, tmp_project, tmp_cache):
        result = asyncio.run(srv._handle_cache_stats({"project_root": tmp_project}))
        data = _parse(result)
        assert data["global"]["blob_count"] == 0

    def test_stats_after_store(self, tmp_project, tmp_cache):
        asyncio.run(srv._handle_cache_store({"path": "src/main.py", "project_root": tmp_project}))
        result = asyncio.run(srv._handle_cache_stats({"project_root": tmp_project}))
        data = _parse(result)
        assert data["global"]["blob_count"] == 1
        assert data["project"]["manifest_files"] == 1


class TestSessionTracking:
    def test_track_and_diff(self, tmp_project, tmp_cache):
        asyncio.run(srv._handle_session_track({
            "session_id": "s1", "path": "src/main.py",
            "project_root": tmp_project,
        }))
        asyncio.run(srv._handle_session_track({
            "session_id": "s1", "path": "README.md",
            "project_root": tmp_project,
        }))
        asyncio.run(srv._handle_session_track({
            "session_id": "s2", "path": "README.md",
            "project_root": tmp_project,
        }))
        asyncio.run(srv._handle_session_track({
            "session_id": "s2", "path": "src/util.py",
            "project_root": tmp_project,
        }))

        result = asyncio.run(srv._handle_session_diff({
            "current_session": "s2",
            "prev_session": "s1",
            "project_root": tmp_project,
        }))
        data = _parse(result)
        assert "src/main.py" in data["only_prev"]
        assert "src/util.py" in data["only_current"]
        assert "README.md" in data["both"]


class TestCacheWarm:
    def test_warm_from_session(self, tmp_project, tmp_cache):
        # Track some files in a session
        asyncio.run(srv._handle_session_track({
            "session_id": "s1", "path": "src/main.py",
            "project_root": tmp_project,
        }))
        asyncio.run(srv._handle_session_track({
            "session_id": "s1", "path": "README.md",
            "project_root": tmp_project,
        }))

        # Warm the cache
        result = asyncio.run(srv._handle_cache_warm({
            "project_root": tmp_project,
        }))
        data = _parse(result)
        assert data["warmed"] == 2
        assert data["missing"] == 0

        # Now both files should be cached
        result = asyncio.run(srv._handle_cache_lookup({"path": "src/main.py", "project_root": tmp_project}))
        assert _parse(result)["hit"] is True

    def test_warm_already_cached(self, tmp_project, tmp_cache):
        asyncio.run(srv._handle_session_track({
            "session_id": "s1", "path": "src/main.py",
            "project_root": tmp_project,
        }))
        asyncio.run(srv._handle_cache_store({"path": "src/main.py", "project_root": tmp_project}))

        result = asyncio.run(srv._handle_cache_warm({"project_root": tmp_project}))
        data = _parse(result)
        assert data["already_valid"] == 1
        assert data["warmed"] == 0


class TestCachePurge:
    def test_purge_project(self, tmp_project, tmp_cache):
        asyncio.run(srv._handle_cache_store({"path": "src/main.py", "project_root": tmp_project}))

        result = asyncio.run(srv._handle_cache_purge({"project_root": tmp_project}))
        data = _parse(result)
        assert data["purged"] is True

        # Lookup should miss now
        result = asyncio.run(srv._handle_cache_lookup({"path": "src/main.py", "project_root": tmp_project}))
        assert _parse(result)["hit"] is False


class TestPathTraversal:
    def test_store_rejects_traversal(self, tmp_project, tmp_cache):
        result = asyncio.run(srv._handle_cache_store({
            "path": "../../etc/passwd",
            "project_root": tmp_project,
        }))
        data = _parse(result)
        assert "error" in data
        assert "traversal" in data["error"].lower()

    def test_lookup_rejects_traversal(self, tmp_project, tmp_cache):
        """Lookup with traversal path should miss, not leak data."""
        result = asyncio.run(srv._handle_cache_lookup({
            "path": "../../../etc/passwd",
            "project_root": tmp_project,
        }))
        data = _parse(result)
        assert data["hit"] is False
