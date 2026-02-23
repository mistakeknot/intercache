"""Embedding persistence — per-project SQLite vector storage with incremental indexing."""

from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from .manifest import _project_hash, DEFAULT_CACHE_DIR

logger = logging.getLogger(__name__)

EMBEDDING_DIM = 768
MODEL_NAME = "nomic-ai/nomic-embed-code-v1"

SCHEMA = """
CREATE TABLE IF NOT EXISTS embeddings (
    path TEXT PRIMARY KEY,
    sha256 TEXT NOT NULL,
    model TEXT NOT NULL,
    vector BLOB NOT NULL,
    updated TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_embeddings_sha256 ON embeddings(sha256);
"""


def vector_to_bytes(vec: np.ndarray) -> bytes:
    """Serialize numpy vector to bytes for SQLite blob storage."""
    return vec.astype(np.float32).tobytes()


def bytes_to_vector(data: bytes) -> np.ndarray:
    """Deserialize bytes from SQLite back to numpy vector."""
    return np.frombuffer(data, dtype=np.float32)


class EmbeddingStore:
    """Per-project embedding storage with lazy model loading."""

    def __init__(self, project_root: str, cache_dir: Path | None = None):
        self.project_root = project_root
        base = (cache_dir or DEFAULT_CACHE_DIR) / "index" / _project_hash(project_root)
        base.mkdir(parents=True, exist_ok=True)
        self.db_path = base / "embeddings.db"
        self._conn: sqlite3.Connection | None = None
        self._embedder = None

    def _connect(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = sqlite3.connect(str(self.db_path))
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA busy_timeout=5000")
            self._conn.executescript(SCHEMA)
            # Check model version — invalidate if changed
            self._check_model_version()
        return self._conn

    def _check_model_version(self) -> None:
        """Invalidate all embeddings if model version changed."""
        conn = self._conn
        row = conn.execute(
            "SELECT value FROM meta WHERE key = 'model_name'"
        ).fetchone()
        if row is None:
            conn.execute(
                "INSERT INTO meta (key, value) VALUES ('model_name', ?)",
                (MODEL_NAME,),
            )
            conn.commit()
        elif row[0] != MODEL_NAME:
            logger.warning(
                "Embedding model changed (%s -> %s), invalidating all embeddings",
                row[0],
                MODEL_NAME,
            )
            conn.execute("DELETE FROM embeddings")
            conn.execute(
                "UPDATE meta SET value = ? WHERE key = 'model_name'", (MODEL_NAME,)
            )
            conn.commit()

    def _ensure_embedder(self):
        """Lazy-load the embedding model."""
        if self._embedder is not None:
            return self._embedder

        # Direct sentence-transformers with nomic-embed-code
        try:
            from sentence_transformers import SentenceTransformer

            class _DirectEmbedder:
                def __init__(self):
                    self._model = SentenceTransformer(MODEL_NAME, trust_remote_code=True)

                def embed(self, text: str) -> np.ndarray:
                    return self.embed_batch([text])[0]

                def embed_batch(self, texts: list[str]) -> np.ndarray:
                    return np.array(
                        self._model.encode(
                            texts,
                            normalize_embeddings=True,
                            show_progress_bar=False,
                        ),
                        dtype=np.float32,
                    )

                def cosine_similarity(self, a: np.ndarray, b: np.ndarray) -> float:
                    return float(np.dot(a, b))

            self._embedder = _DirectEmbedder()
            return self._embedder
        except ImportError:
            raise RuntimeError(
                "No embedding backend available. Install intersearch or sentence-transformers."
            )

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    def index_file(self, path: str, content: str, sha256: str) -> bool:
        """Index a file's content. Returns True if newly indexed, False if already up-to-date."""
        conn = self._connect()
        row = conn.execute(
            "SELECT sha256 FROM embeddings WHERE path = ?", (path,)
        ).fetchone()

        if row and row[0] == sha256:
            return False  # Already indexed with same content

        embedder = self._ensure_embedder()
        vec = embedder.embed(content)
        now = datetime.now(timezone.utc).isoformat()

        conn.execute(
            "INSERT INTO embeddings (path, sha256, model, vector, updated) "
            "VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(path) DO UPDATE SET sha256=?, model=?, vector=?, updated=?",
            (path, sha256, MODEL_NAME, vector_to_bytes(vec), now,
             sha256, MODEL_NAME, vector_to_bytes(vec), now),
        )
        conn.commit()
        return True

    def query(self, query_text: str, top_k: int = 10) -> list[dict]:
        """Semantic search: return top-K files by cosine similarity.

        Returns [{path, sha256, score, updated}, ...] sorted by score descending.
        """
        conn = self._connect()
        embedder = self._ensure_embedder()
        query_vec = embedder.embed(query_text)

        rows = conn.execute(
            "SELECT path, sha256, vector, updated FROM embeddings"
        ).fetchall()

        if not rows:
            return []

        results = []
        for path, sha256, vec_bytes, updated in rows:
            vec = bytes_to_vector(vec_bytes)
            score = float(np.dot(query_vec, vec))
            results.append({
                "path": path,
                "sha256": sha256,
                "score": score,
                "updated": updated,
            })

        results.sort(key=lambda r: r["score"], reverse=True)
        return results[:top_k]

    def invalidate(self, path: str) -> bool:
        """Remove embedding for a path. Returns True if it existed."""
        conn = self._connect()
        cursor = conn.execute("DELETE FROM embeddings WHERE path = ?", (path,))
        conn.commit()
        return cursor.rowcount > 0

    def count(self) -> int:
        """Return total number of indexed files."""
        conn = self._connect()
        row = conn.execute("SELECT COUNT(*) FROM embeddings").fetchone()
        return row[0]

    def stale_paths(self, manifest_entries: list[dict]) -> list[str]:
        """Return paths whose sha256 differs between embedding store and manifest."""
        conn = self._connect()
        stale = []
        for entry in manifest_entries:
            row = conn.execute(
                "SELECT sha256 FROM embeddings WHERE path = ?", (entry["path"],)
            ).fetchone()
            if row is None or row[0] != entry["sha256"]:
                stale.append(entry["path"])
        return stale
