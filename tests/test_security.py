"""Security tests — path traversal, input validation."""

import os

import pytest

from intercache.manifest import Manifest
from intercache.server import _safe_resolve


class TestPathTraversal:
    def test_resolve_normal_path(self, tmp_path):
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "main.py").write_text("hello")
        result = _safe_resolve(str(tmp_path), "src/main.py")
        assert result is not None
        assert result.endswith("src/main.py")

    def test_reject_parent_traversal(self, tmp_path):
        assert _safe_resolve(str(tmp_path), "../../etc/passwd") is None

    def test_reject_absolute_path(self, tmp_path):
        assert _safe_resolve(str(tmp_path), "/etc/passwd") is None

    def test_reject_symlink_escape(self, tmp_path):
        """Symlink inside project pointing outside should be rejected."""
        outside = tmp_path / "outside"
        outside.mkdir()
        (outside / "secret.txt").write_text("sensitive data")

        project = tmp_path / "project"
        project.mkdir()
        (project / "link").symlink_to(outside)

        result = _safe_resolve(str(project), "link/secret.txt")
        assert result is None  # Should NOT resolve outside project

    def test_manifest_validate_rejects_traversal(self, tmp_path):
        project = tmp_path / "project"
        project.mkdir()
        cache = tmp_path / "cache"

        m = Manifest(str(project), cache_dir=cache)
        try:
            # Even if someone manually inserts a traversal path in the DB
            m.update("../../etc/passwd", "fakehash", 0.0, 0)
            valid, _ = m.validate("../../etc/passwd")
            assert valid is False
        finally:
            m.close()
