#!/usr/bin/env bash
# Launcher for intercache MCP server: checks prerequisites before starting.
# Needs uv (Python package manager) to function.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PLUGIN_ROOT="$(dirname "$SCRIPT_DIR")"

if ! command -v uv &>/dev/null; then
    echo "uv not found — install uv to use the intercache MCP server." >&2
    echo "intercache provides cross-session caching but is not required." >&2
    exit 0
fi

exec uv run --directory "$PLUGIN_ROOT" intercache-mcp "$@"
