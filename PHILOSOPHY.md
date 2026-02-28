# intercache Philosophy

## Purpose
Cross-session semantic cache for Claude Code. Content-addressed blob storage, per-project manifests, and session tracking — reduces cold start time and eliminates redundant file reads across sessions.

## North Star
Eliminate redundant reads — if an agent read it before, it shouldn't cost tokens again.

## Working Priorities
- Cache hit rate (maximize reuse across sessions)
- Invalidation correctness (never serve stale content)
- Cold start reduction

## Brainstorming Doctrine
1. Start from outcomes and failure modes, not implementation details.
2. Generate at least three options: conservative, balanced, and aggressive.
3. Explicitly call out assumptions, unknowns, and dependency risk across modules.
4. Prefer ideas that improve clarity, reversibility, and operational visibility.

## Planning Doctrine
1. Convert selected direction into small, testable, reversible slices.
2. Define acceptance criteria, verification steps, and rollback path for each slice.
3. Sequence dependencies explicitly and keep integration contracts narrow.
4. Reserve optimization work until correctness and reliability are proven.

## Decision Filters
- Does this reduce tokens spent re-reading unchanged content?
- Does this guarantee freshness (no stale cache hits)?
- Is the storage overhead proportional to the savings?
- Does this work transparently without agent cooperation?
