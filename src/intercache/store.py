"""Content-addressed blob store with SHA256 keying and 2-char prefix sharding."""

from __future__ import annotations

import hashlib
import logging
import os
import shutil
import tempfile
from pathlib import Path

logger = logging.getLogger(__name__)

DEFAULT_CACHE_DIR = Path.home() / ".intercache"


class BlobStore:
    """SHA256-keyed blob storage with atomic writes and prefix sharding."""

    def __init__(self, root: Path | None = None):
        self.root = (root or DEFAULT_CACHE_DIR) / "blobs"
        self.root.mkdir(parents=True, exist_ok=True)

    def _blob_path(self, sha256: str) -> Path:
        """Return sharded path: blobs/ab/cd1234..."""
        return self.root / sha256[:2] / sha256[2:]

    @staticmethod
    def hash_content(content: bytes) -> str:
        """Compute SHA256 hex digest."""
        return hashlib.sha256(content).hexdigest()

    def store(self, content: bytes) -> str:
        """Store content, return SHA256 hash. Atomic via tmp + rename."""
        sha256 = self.hash_content(content)
        blob_path = self._blob_path(sha256)

        if blob_path.exists():
            return sha256  # Already stored (dedup)

        blob_path.parent.mkdir(parents=True, exist_ok=True)

        # Atomic write: write to temp file in same directory, then rename
        fd, tmp_path = tempfile.mkstemp(dir=blob_path.parent, suffix=".tmp")
        closed = False
        try:
            os.write(fd, content)
            os.close(fd)
            closed = True
            os.rename(tmp_path, blob_path)
        except BaseException:
            if not closed:
                os.close(fd)
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise

        return sha256

    def lookup(self, sha256: str) -> bytes | None:
        """Return blob content by hash, or None if not cached."""
        blob_path = self._blob_path(sha256)
        if not blob_path.exists():
            return None
        return blob_path.read_bytes()

    def delete(self, sha256: str) -> bool:
        """Delete a blob. Returns True if it existed."""
        blob_path = self._blob_path(sha256)
        if blob_path.exists():
            blob_path.unlink()
            # Clean up empty shard directory
            try:
                blob_path.parent.rmdir()
            except OSError:
                pass
            return True
        return False

    def stats(self) -> dict:
        """Return blob count and total size."""
        count = 0
        total_bytes = 0
        for shard in self.root.iterdir():
            if shard.is_dir() and len(shard.name) == 2:
                for blob in shard.iterdir():
                    if not blob.name.endswith(".tmp"):
                        count += 1
                        total_bytes += blob.stat().st_size
        return {"blob_count": count, "total_bytes": total_bytes}

    def purge(self) -> int:
        """Delete all blobs. Returns count deleted."""
        count = 0
        for shard in list(self.root.iterdir()):
            if shard.is_dir() and len(shard.name) == 2:
                for blob in list(shard.iterdir()):
                    blob.unlink()
                    count += 1
                try:
                    shard.rmdir()
                except OSError:
                    pass
        return count
