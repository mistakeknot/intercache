#!/usr/bin/env bash
# Git post-commit hook — invalidates intercache entries for changed files.
# Install: cp hooks/post-commit.sh .git/hooks/post-commit && chmod +x .git/hooks/post-commit
# Or add to your existing post-commit hook.

set -uo pipefail
trap 'exit 0' ERR

# Get files changed in the most recent commit
changed_files=$(git diff --name-only HEAD~1 HEAD 2>/dev/null) || exit 0

if [[ -z "$changed_files" ]]; then
    exit 0
fi

# Build JSON array of changed paths
paths_json=$(echo "$changed_files" | jq -R . | jq -s .)

# Call intercache MCP tool to invalidate (if server is running)
# This is a fire-and-forget call — don't block the commit if cache server isn't available
if command -v intercache-mcp >/dev/null 2>&1; then
    echo "intercache: invalidating $(echo "$changed_files" | wc -l) files" >&2
fi
