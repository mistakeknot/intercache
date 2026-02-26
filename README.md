# intercache

Cross-session semantic cache for Claude Code.

## What this does

intercache stores and retrieves file content across Claude Code sessions using content-addressed blob storage. When Claude reads a file that hasn't changed since last session, the cached version loads instantly instead of re-reading from disk. Session tracking records which files are accessed, so future sessions can pre-warm the most relevant files.

Files are stored by SHA256 hash with 2-character prefix sharding, so identical content is never stored twice. Per-project manifests track mtime and size for fast invalidation without re-hashing.

## Installation

intercache is an internal plugin in the [Demarch](https://github.com/mistakeknot/Demarch) monorepo. Install from the interagency marketplace:

```bash
/plugin marketplace add mistakeknot/interagency-marketplace
/plugin install intercache
```

## MCP Tools

| Tool | Purpose |
|------|---------|
| `cache_lookup` | Return cached content if file unchanged |
| `cache_store` | Store file content with SHA256 dedup |
| `cache_invalidate` | Invalidate by path, pattern, or project |
| `cache_warm` | Pre-warm cache from recent sessions |
| `cache_stats` | Hit rates, sizes, file counts |
| `session_track` | Record file accesses for cross-session dedup |
| `session_diff` | Compare accesses between sessions |
| `cache_purge` | Wipe cached data (per-project or global) |

## Architecture

```
src/intercache/
  server.py      MCP server (8 tools)
  store.py       Content-addressed blob store (SHA256, 2-char prefix sharding)
  manifest.py    Per-project file manifest (SQLite, mtime+size validation)
  session.py     Session tracking (JSONL read logs, cross-session dedup)
hooks/
  post-commit.sh Git hook for cache invalidation on commit
scripts/
  launch-intercache.sh  MCP server launcher
```

All data stored at `~/.intercache/`:
- `blobs/` — Content-addressed blob store
- `index/<project-hash>/` — Per-project manifests and session logs

## Changes in v0.2.0

Embedding tools (`embedding_index`, `embedding_query`) moved to [intersearch](https://github.com/mistakeknot/intersearch), which provides persistent vector storage with the nomic-embed-text-v1.5 model. intercache now focuses exclusively on file caching and session tracking.
