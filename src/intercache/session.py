"""Session tracking — records what files were accessed per session for cross-session dedup.

Uses SQLite (WAL mode) instead of JSONL for safe concurrent writes from multiple sessions.
"""

from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .manifest import _project_hash, DEFAULT_CACHE_DIR

logger = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS session_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    path TEXT NOT NULL,
    action TEXT NOT NULL DEFAULT 'read',
    timestamp TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_session_log_session ON session_log(session_id);
CREATE INDEX IF NOT EXISTS idx_session_log_path ON session_log(path);
"""


class SessionTracker:
    """Per-project session tracking backed by SQLite."""

    def __init__(self, project_root: str, cache_dir: Path | None = None):
        self.project_root = project_root
        base = (cache_dir or DEFAULT_CACHE_DIR) / "index" / _project_hash(project_root)
        base.mkdir(parents=True, exist_ok=True)
        self.db_path = base / "sessions.db"
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

    def track(self, session_id: str, path: str, action: str = "read") -> None:
        """Record a file access event."""
        now = datetime.now(timezone.utc).isoformat()
        conn = self._connect()
        conn.execute(
            "INSERT INTO session_log (session_id, path, action, timestamp) VALUES (?, ?, ?, ?)",
            (session_id, path, action, now),
        )
        conn.commit()

    def get_session_files(self, session_id: str) -> list[str]:
        """Return unique file paths accessed in a given session."""
        conn = self._connect()
        rows = conn.execute(
            "SELECT DISTINCT path FROM session_log WHERE session_id = ? ORDER BY path",
            (session_id,),
        ).fetchall()
        return [r[0] for r in rows]

    def get_recent_files(self, n_sessions: int = 3) -> list[str]:
        """Return unique file paths from the last N sessions (most recent first)."""
        conn = self._connect()
        # Find the N most recent session IDs
        session_rows = conn.execute(
            "SELECT session_id, MAX(timestamp) as last_ts "
            "FROM session_log GROUP BY session_id ORDER BY last_ts DESC LIMIT ?",
            (n_sessions,),
        ).fetchall()

        if not session_rows:
            return []

        sids = [r[0] for r in session_rows]
        placeholders = ",".join("?" for _ in sids)
        rows = conn.execute(
            f"SELECT DISTINCT path FROM session_log WHERE session_id IN ({placeholders}) ORDER BY path",
            sids,
        ).fetchall()
        return [r[0] for r in rows]

    def session_diff(self, current_session: str, prev_session: str) -> dict:
        """Compare file accesses between two sessions.

        Returns {only_prev: [...], only_current: [...], both: [...]}.
        """
        current = set(self.get_session_files(current_session))
        prev = set(self.get_session_files(prev_session))
        return {
            "only_prev": sorted(prev - current),
            "only_current": sorted(current - prev),
            "both": sorted(current & prev),
        }

    def recent_session_ids(self, n: int = 5) -> list[str]:
        """Return the N most recent session IDs."""
        conn = self._connect()
        rows = conn.execute(
            "SELECT session_id, MAX(timestamp) as last_ts "
            "FROM session_log GROUP BY session_id ORDER BY last_ts DESC LIMIT ?",
            (n,),
        ).fetchall()
        return [r[0] for r in rows]

    def prune(self, keep_sessions: int = 10) -> int:
        """Remove log entries older than the last N sessions. Returns lines removed."""
        conn = self._connect()
        recent_sids = self.recent_session_ids(keep_sessions)
        if not recent_sids:
            return 0

        placeholders = ",".join("?" for _ in recent_sids)
        cursor = conn.execute(
            f"DELETE FROM session_log WHERE session_id NOT IN ({placeholders})",
            recent_sids,
        )
        conn.commit()
        return cursor.rowcount
