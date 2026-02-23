"""Per-project file manifest — tracks path → SHA256 mappings with mtime/size validation."""

from __future__ import annotations

import hashlib
import logging
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

DEFAULT_CACHE_DIR = Path.home() / ".intercache"

SCHEMA = """
CREATE TABLE IF NOT EXISTS files (
    path TEXT PRIMARY KEY,
    sha256 TEXT NOT NULL,
    mtime REAL NOT NULL,
    size INTEGER NOT NULL,
    last_accessed TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_files_sha256 ON files(sha256);
"""


def _project_hash(project_root: str) -> str:
    """Deterministic hash of project root path for directory naming."""
    return hashlib.sha256(project_root.encode()).hexdigest()[:16]


class Manifest:
    """Per-project file manifest backed by SQLite."""

    def __init__(self, project_root: str, cache_dir: Path | None = None):
        self.project_root = project_root
        base = (cache_dir or DEFAULT_CACHE_DIR) / "index" / _project_hash(project_root)
        base.mkdir(parents=True, exist_ok=True)
        self.db_path = base / "manifest.db"
        self._conn: sqlite3.Connection | None = None

    def _connect(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = sqlite3.connect(str(self.db_path))
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA busy_timeout=5000")
            self._conn.executescript(SCHEMA)
        return self._conn

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    def update(self, path: str, sha256: str, mtime: float, size: int) -> None:
        """Upsert a file mapping."""
        now = datetime.now(timezone.utc).isoformat()
        conn = self._connect()
        conn.execute(
            "INSERT INTO files (path, sha256, mtime, size, last_accessed) "
            "VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(path) DO UPDATE SET sha256=?, mtime=?, size=?, last_accessed=?",
            (path, sha256, mtime, size, now, sha256, mtime, size, now),
        )
        conn.commit()

    def lookup(self, path: str) -> dict | None:
        """Return {sha256, mtime, size, last_accessed} or None."""
        conn = self._connect()
        row = conn.execute(
            "SELECT sha256, mtime, size, last_accessed FROM files WHERE path = ?",
            (path,),
        ).fetchone()
        if row is None:
            return None
        return {
            "sha256": row[0],
            "mtime": row[1],
            "size": row[2],
            "last_accessed": row[3],
        }

    def touch(self, path: str) -> None:
        """Update last_accessed timestamp without changing other fields."""
        now = datetime.now(timezone.utc).isoformat()
        conn = self._connect()
        conn.execute("UPDATE files SET last_accessed = ? WHERE path = ?", (now, path))
        conn.commit()

    @staticmethod
    def _safe_resolve(project_root: str, path: str) -> str | None:
        """Resolve path safely within project root. Returns None if path escapes."""
        full = os.path.realpath(os.path.join(project_root, path))
        root = os.path.realpath(project_root)
        if not full.startswith(root + os.sep) and full != root:
            return None
        return full

    def validate(self, path: str) -> tuple[bool, str | None]:
        """Check if cached entry matches current file on disk.

        Returns (valid, current_sha256_or_None).
        - valid=True means mtime+size match (no re-hash needed).
        - valid=False with sha256 means file changed (needs re-cache).
        - valid=False with None means file doesn't exist or isn't cached.
        """
        entry = self.lookup(path)
        if entry is None:
            return False, None

        full_path = self._safe_resolve(self.project_root, path)
        if full_path is None:
            return False, None

        try:
            st = os.stat(full_path)
        except OSError:
            return False, None

        # Fast path: mtime + size unchanged → assume content unchanged
        if st.st_mtime == entry["mtime"] and st.st_size == entry["size"]:
            self.touch(path)
            return True, entry["sha256"]

        # Slow path: re-hash to check actual content
        try:
            with open(full_path, "rb") as f:
                current_sha256 = hashlib.sha256(f.read()).hexdigest()
        except OSError:
            return False, None

        if current_sha256 == entry["sha256"]:
            # Content same despite mtime change — update mtime in manifest
            self.update(path, current_sha256, st.st_mtime, st.st_size)
            return True, current_sha256

        return False, current_sha256

    def invalidate(self, path_pattern: str) -> int:
        """Delete entries matching a path pattern (SQL LIKE). Returns count."""
        conn = self._connect()
        cursor = conn.execute(
            "DELETE FROM files WHERE path LIKE ?", (path_pattern,)
        )
        conn.commit()
        return cursor.rowcount

    def invalidate_paths(self, paths: list[str]) -> int:
        """Delete specific paths. Returns count deleted."""
        conn = self._connect()
        count = 0
        for path in paths:
            cursor = conn.execute("DELETE FROM files WHERE path = ?", (path,))
            count += cursor.rowcount
        conn.commit()
        return count

    def list_stale(self, max_age_days: int = 7) -> list[str]:
        """Return paths not accessed within max_age_days."""
        conn = self._connect()
        cutoff = datetime.now(timezone.utc).isoformat()
        # Simple approach: compare ISO strings (works because ISO 8601 sorts lexically)
        from datetime import timedelta

        cutoff_dt = datetime.now(timezone.utc) - timedelta(days=max_age_days)
        cutoff_str = cutoff_dt.isoformat()
        rows = conn.execute(
            "SELECT path FROM files WHERE last_accessed < ?", (cutoff_str,)
        ).fetchall()
        return [r[0] for r in rows]

    def all_entries(self) -> list[dict]:
        """Return all manifest entries."""
        conn = self._connect()
        rows = conn.execute(
            "SELECT path, sha256, mtime, size, last_accessed FROM files"
        ).fetchall()
        return [
            {
                "path": r[0],
                "sha256": r[1],
                "mtime": r[2],
                "size": r[3],
                "last_accessed": r[4],
            }
            for r in rows
        ]

    def count(self) -> int:
        """Return total number of tracked files."""
        conn = self._connect()
        row = conn.execute("SELECT COUNT(*) FROM files").fetchone()
        return row[0]
