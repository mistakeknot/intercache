"""Tests for the per-project file manifest."""

import os
import tempfile
import time
from pathlib import Path

import pytest

from intercache.manifest import Manifest


@pytest.fixture
def project_dir(tmp_path):
    """Create a temp project directory with some files."""
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "main.py").write_text("print('hello')")
    (tmp_path / "README.md").write_text("# Test")
    return str(tmp_path)


@pytest.fixture
def manifest(project_dir, tmp_path):
    cache_dir = tmp_path / "cache"
    m = Manifest(project_dir, cache_dir=cache_dir)
    yield m
    m.close()


class TestManifest:
    def test_update_and_lookup(self, manifest):
        manifest.update("src/main.py", "abc123", 1000.0, 42)
        entry = manifest.lookup("src/main.py")
        assert entry is not None
        assert entry["sha256"] == "abc123"
        assert entry["mtime"] == 1000.0
        assert entry["size"] == 42

    def test_lookup_missing(self, manifest):
        assert manifest.lookup("nonexistent.py") is None

    def test_upsert(self, manifest):
        manifest.update("file.py", "hash1", 1.0, 10)
        manifest.update("file.py", "hash2", 2.0, 20)
        entry = manifest.lookup("file.py")
        assert entry["sha256"] == "hash2"
        assert entry["size"] == 20

    def test_validate_unchanged_file(self, manifest, project_dir):
        path = "src/main.py"
        full_path = os.path.join(project_dir, path)
        st = os.stat(full_path)
        with open(full_path, "rb") as f:
            import hashlib
            sha256 = hashlib.sha256(f.read()).hexdigest()
        manifest.update(path, sha256, st.st_mtime, st.st_size)

        valid, returned_sha = manifest.validate(path)
        assert valid is True
        assert returned_sha == sha256

    def test_validate_changed_file(self, manifest, project_dir):
        path = "src/main.py"
        manifest.update(path, "old_hash", 0.0, 1)

        valid, current_sha = manifest.validate(path)
        assert valid is False

    def test_invalidate_by_pattern(self, manifest):
        manifest.update("src/a.py", "h1", 1.0, 1)
        manifest.update("src/b.py", "h2", 1.0, 2)
        manifest.update("docs/c.md", "h3", 1.0, 3)

        count = manifest.invalidate("src/%")
        assert count == 2
        assert manifest.lookup("src/a.py") is None
        assert manifest.lookup("docs/c.md") is not None

    def test_invalidate_paths(self, manifest):
        manifest.update("a.py", "h1", 1.0, 1)
        manifest.update("b.py", "h2", 1.0, 2)
        count = manifest.invalidate_paths(["a.py"])
        assert count == 1
        assert manifest.lookup("a.py") is None
        assert manifest.lookup("b.py") is not None

    def test_count(self, manifest):
        assert manifest.count() == 0
        manifest.update("a.py", "h1", 1.0, 1)
        manifest.update("b.py", "h2", 1.0, 2)
        assert manifest.count() == 2

    def test_all_entries(self, manifest):
        manifest.update("a.py", "h1", 1.0, 10)
        manifest.update("b.py", "h2", 2.0, 20)
        entries = manifest.all_entries()
        assert len(entries) == 2
        paths = {e["path"] for e in entries}
        assert paths == {"a.py", "b.py"}
