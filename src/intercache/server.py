"""Intercache MCP server — cross-session semantic cache for Claude Code.

Exposes tools for content-addressed caching, session tracking, and embedding search.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import signal
import sys
from pathlib import Path
from typing import Any

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool

from .manifest import Manifest
from .session import SessionTracker
from .store import BlobStore, DEFAULT_CACHE_DIR

logger = logging.getLogger(__name__)

# Cache open manifests/sessions per project root
_manifests: dict[str, Manifest] = {}
_sessions: dict[str, SessionTracker] = {}
_blob_store: BlobStore | None = None


def _get_blob_store() -> BlobStore:
    global _blob_store
    if _blob_store is None:
        _blob_store = BlobStore()
    return _blob_store


def _get_manifest(project_root: str) -> Manifest:
    if project_root not in _manifests:
        _manifests[project_root] = Manifest(project_root)
    return _manifests[project_root]


def _get_session(project_root: str) -> SessionTracker:
    if project_root not in _sessions:
        _sessions[project_root] = SessionTracker(project_root)
    return _sessions[project_root]


def _ok(data: Any) -> list[TextContent]:
    """Return a JSON text content response."""
    return [TextContent(type="text", text=json.dumps(data, indent=2))]


def _err(msg: str) -> list[TextContent]:
    """Return an error text content response."""
    return [TextContent(type="text", text=json.dumps({"error": msg}))]


def create_server() -> Server:
    """Create and configure the MCP server with all tools."""
    server = Server("intercache")

    @server.list_tools()
    async def list_tools() -> list[Tool]:
        return [
            Tool(
                name="cache_lookup",
                description=(
                    "Look up a file in the cache. Returns cached content if the file "
                    "hasn't changed (validated by mtime+size, falling back to SHA256). "
                    "Returns a cache miss if the file changed or isn't cached."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "path": {
                            "type": "string",
                            "description": "File path relative to project root.",
                        },
                        "project_root": {
                            "type": "string",
                            "description": "Absolute path to the project root.",
                        },
                    },
                    "required": ["path", "project_root"],
                },
            ),
            Tool(
                name="cache_store",
                description=(
                    "Store a file's content in the cache. Computes SHA256, stores the "
                    "blob (deduplicating identical content), and updates the manifest."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "path": {
                            "type": "string",
                            "description": "File path relative to project root.",
                        },
                        "project_root": {
                            "type": "string",
                            "description": "Absolute path to the project root.",
                        },
                    },
                    "required": ["path", "project_root"],
                },
            ),
            Tool(
                name="cache_invalidate",
                description=(
                    "Invalidate cached entries. Accepts a list of specific paths or "
                    "a LIKE pattern (e.g., 'src/%'). Removes manifest entries and blobs."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "paths": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Specific file paths to invalidate.",
                        },
                        "pattern": {
                            "type": "string",
                            "description": "SQL LIKE pattern to invalidate (e.g., 'src/%').",
                        },
                        "project_root": {
                            "type": "string",
                            "description": "Absolute path to the project root.",
                        },
                    },
                    "required": ["project_root"],
                },
            ),
            Tool(
                name="cache_warm",
                description=(
                    "Pre-warm the cache for a project by validating and re-caching files "
                    "from recent sessions. Reads files from disk and stores them if not "
                    "already cached."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "project_root": {
                            "type": "string",
                            "description": "Absolute path to the project root.",
                        },
                        "n_sessions": {
                            "type": "integer",
                            "description": "Number of recent sessions to warm from (default 3).",
                            "default": 3,
                        },
                        "max_files": {
                            "type": "integer",
                            "description": "Maximum files to warm (default 1000).",
                            "default": 1000,
                        },
                    },
                    "required": ["project_root"],
                },
            ),
            Tool(
                name="cache_stats",
                description=(
                    "Return cache statistics: blob count, total size, per-project "
                    "manifest counts, and hit/miss information."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "project_root": {
                            "type": "string",
                            "description": "Scope stats to a specific project. Omit for global.",
                        },
                    },
                },
            ),
            Tool(
                name="session_track",
                description=(
                    "Record that a file was accessed in the current session. Used for "
                    "cross-session dedup and cache warming."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "session_id": {
                            "type": "string",
                            "description": "Current session identifier.",
                        },
                        "path": {
                            "type": "string",
                            "description": "File path relative to project root.",
                        },
                        "action": {
                            "type": "string",
                            "description": "Access type: 'read' or 'write' (default 'read').",
                            "default": "read",
                        },
                        "project_root": {
                            "type": "string",
                            "description": "Absolute path to the project root.",
                        },
                    },
                    "required": ["session_id", "path", "project_root"],
                },
            ),
            Tool(
                name="session_diff",
                description=(
                    "Compare file accesses between two sessions. Shows files only in "
                    "the previous session, only in current, and in both."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "current_session": {
                            "type": "string",
                            "description": "Current session ID.",
                        },
                        "prev_session": {
                            "type": "string",
                            "description": "Previous session ID to compare against.",
                        },
                        "project_root": {
                            "type": "string",
                            "description": "Absolute path to the project root.",
                        },
                    },
                    "required": ["current_session", "prev_session", "project_root"],
                },
            ),
            Tool(
                name="cache_purge",
                description=(
                    "Purge all cached data for a project or globally. Use for sensitive "
                    "repos or to reclaim disk space."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "project_root": {
                            "type": "string",
                            "description": "Purge only this project's data. Omit for global purge.",
                        },
                    },
                },
            ),
        ]

    @server.call_tool()
    async def call_tool(name: str, arguments: dict) -> list[TextContent]:
        try:
            if name == "cache_lookup":
                return await _handle_cache_lookup(arguments)
            elif name == "cache_store":
                return await _handle_cache_store(arguments)
            elif name == "cache_invalidate":
                return await _handle_cache_invalidate(arguments)
            elif name == "cache_warm":
                return await _handle_cache_warm(arguments)
            elif name == "cache_stats":
                return await _handle_cache_stats(arguments)
            elif name == "session_track":
                return await _handle_session_track(arguments)
            elif name == "session_diff":
                return await _handle_session_diff(arguments)
            elif name == "cache_purge":
                return await _handle_cache_purge(arguments)
            else:
                return _err(f"Unknown tool: {name}")
        except Exception as e:
            logger.exception("Tool %s failed", name)
            return _err(f"{type(e).__name__}: {e}")

    return server


# ── Tool handlers ──────────────────────────────────────────────────────────


async def _handle_cache_lookup(args: dict) -> list[TextContent]:
    path = args["path"]
    project_root = args["project_root"]
    manifest = _get_manifest(project_root)

    valid, sha256 = manifest.validate(path)
    if not valid:
        return _ok({"hit": False, "path": path, "reason": "miss_or_changed"})

    blob_store = _get_blob_store()
    content = blob_store.lookup(sha256)
    if content is None:
        return _ok({"hit": False, "path": path, "reason": "blob_missing"})

    return _ok({
        "hit": True,
        "path": path,
        "sha256": sha256,
        "size": len(content),
        "content": content.decode("utf-8", errors="replace"),
    })


def _safe_resolve(project_root: str, path: str) -> str | None:
    """Resolve path safely within project root. Returns None if path escapes."""
    full = os.path.realpath(os.path.join(project_root, path))
    root = os.path.realpath(project_root)
    if not full.startswith(root + os.sep) and full != root:
        return None
    return full


async def _handle_cache_store(args: dict) -> list[TextContent]:
    path = args["path"]
    project_root = args["project_root"]
    full_path = _safe_resolve(project_root, path)
    if full_path is None:
        return _err(f"Path traversal denied: {path}")

    try:
        with open(full_path, "rb") as f:
            content = f.read()
    except OSError as e:
        return _err(f"Cannot read file: {e}")

    st = os.stat(full_path)
    blob_store = _get_blob_store()
    sha256 = blob_store.store(content)

    manifest = _get_manifest(project_root)
    manifest.update(path, sha256, st.st_mtime, st.st_size)

    return _ok({
        "stored": True,
        "path": path,
        "sha256": sha256,
        "size": len(content),
    })


async def _handle_cache_invalidate(args: dict) -> list[TextContent]:
    project_root = args["project_root"]
    manifest = _get_manifest(project_root)
    count = 0

    paths = args.get("paths")
    pattern = args.get("pattern")

    if paths:
        count += manifest.invalidate_paths(paths)
    if pattern:
        count += manifest.invalidate(pattern)

    return _ok({"invalidated": count, "project_root": project_root})


async def _handle_cache_warm(args: dict) -> list[TextContent]:
    project_root = args["project_root"]
    n_sessions = args.get("n_sessions", 3)
    max_files = args.get("max_files", 1000)

    session = _get_session(project_root)
    manifest = _get_manifest(project_root)
    blob_store = _get_blob_store()

    # Get files from recent sessions
    files = session.get_recent_files(n_sessions)[:max_files]

    warmed = 0
    already_valid = 0
    missing = 0

    for path in files:
        valid, sha256 = manifest.validate(path)
        if valid and sha256 and blob_store.lookup(sha256) is not None:
            already_valid += 1
            continue

        # Re-cache from disk
        full_path = os.path.join(project_root, path)
        try:
            with open(full_path, "rb") as f:
                content = f.read()
            st = os.stat(full_path)
            new_sha256 = blob_store.store(content)
            manifest.update(path, new_sha256, st.st_mtime, st.st_size)
            warmed += 1
        except OSError:
            missing += 1

    return _ok({
        "warmed": warmed,
        "already_valid": already_valid,
        "missing": missing,
        "total_checked": len(files),
    })


async def _handle_cache_stats(args: dict) -> list[TextContent]:
    blob_store = _get_blob_store()
    blob_stats = blob_store.stats()

    result: dict[str, Any] = {"global": blob_stats}

    project_root = args.get("project_root")
    if project_root:
        manifest = _get_manifest(project_root)
        result["project"] = {
            "project_root": project_root,
            "manifest_files": manifest.count(),
        }

    return _ok(result)


async def _handle_session_track(args: dict) -> list[TextContent]:
    project_root = args["project_root"]
    session_id = args["session_id"]
    path = args["path"]
    action = args.get("action", "read")

    session = _get_session(project_root)
    session.track(session_id, path, action)

    return _ok({"tracked": True, "session_id": session_id, "path": path, "action": action})


async def _handle_session_diff(args: dict) -> list[TextContent]:
    project_root = args["project_root"]
    session = _get_session(project_root)
    diff = session.session_diff(args["current_session"], args["prev_session"])
    return _ok(diff)


async def _handle_cache_purge(args: dict) -> list[TextContent]:
    project_root = args.get("project_root")

    if project_root:
        # Purge specific project
        manifest = _get_manifest(project_root)

        entries = manifest.all_entries()
        manifest.invalidate("%")
        manifest.close()

        # Remove from caches
        _manifests.pop(project_root, None)
        _sessions.pop(project_root, None)

        return _ok({
            "purged": True,
            "project_root": project_root,
            "files_removed": len(entries),
        })
    else:
        # Global purge
        blob_store = _get_blob_store()
        count = blob_store.purge()

        # Close all project connections
        for m in _manifests.values():
            m.close()
        _manifests.clear()
        _sessions.clear()

        return _ok({"purged": True, "scope": "global", "blobs_removed": count})


# ── Entry point ────────────────────────────────────────────────────────────


def main():
    """Run the intercache MCP server on stdio."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        stream=sys.stderr,
    )

    server = create_server()

    async def run():
        async with stdio_server() as (read_stream, write_stream):
            await server.run(read_stream, write_stream, server.create_initialization_options())

    # Handle signals for clean shutdown
    loop = asyncio.new_event_loop()

    def shutdown(sig):
        logger.info("Received %s, shutting down", sig.name)
        for m in _manifests.values():
            m.close()
        loop.stop()

    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, shutdown, sig)

    try:
        loop.run_until_complete(run())
    finally:
        loop.close()


if __name__ == "__main__":
    main()
