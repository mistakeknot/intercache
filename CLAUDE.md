# Intercache

Cross-session semantic cache for Claude Code. Content-addressed blob storage, per-project manifests, session tracking, and persistent embeddings.

## MCP Server

Python MCP server at `src/intercache/server.py`. Run with `uv run intercache-mcp`.

## Key Files

- `src/intercache/server.py` — MCP server entrypoint (10 tools)
- `src/intercache/store.py` — Content-addressed blob store (SHA256 keying, 2-char prefix sharding)
- `src/intercache/manifest.py` — Per-project file manifest (SQLite, mtime+size validation)
- `src/intercache/session.py` — Session tracking (JSONL read logs, cross-session dedup)
- `src/intercache/embeddings.py` — Embedding persistence (SQLite vectors, lazy model loading)
- `hooks/post-commit.sh` — Git hook for cache invalidation

## Storage

All data stored at `~/.intercache/`:
- `blobs/` — Content-addressed blob store (SHA256 → file content)
- `index/<project-hash>/` — Per-project manifest, embeddings, session logs

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
| `embedding_index` | Index cached files for semantic search |
| `embedding_query` | Semantic search across indexed files |
| `cache_purge` | Wipe cached data (per-project or global) |

## Dependencies

- Core: `mcp`, `numpy`
- Embeddings (optional): `sentence-transformers` + `einops` (for nomic-embed-code model)

## Testing

```bash
cd interverse/intercache
uv run pytest tests/ -v
```
