# Player Stats Collector Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Collect LG Twins hitter stat payloads needed for lineup scoring snapshots.

**Architecture:** Player stat collection must support season stats, recent rolling stats, and pitcher-handedness splits when available.

**Tech Stack:** Python, httpx, pytest.

---

## Scope

Fetch raw player stat payloads. Do not compute model scores in this task.

## Files

- Create: `apps/api/app/ingestion/collectors/player_stats.py`
- Create: `apps/api/tests/test_player_stats_collector.py`
- Modify: `docs/data-sources/kbo-source-matrix.md`

## Steps

- [ ] Implement `collect_lg_hitter_season_stats(season)`.
- [ ] Implement `collect_lg_hitter_recent_stats(as_of_date, windows=[14, 30])`.
- [ ] Implement `collect_lg_hitter_split_stats(season)` when the selected source supports handedness splits.
- [ ] Save every response through the raw ingestion store.
- [ ] Mark unsupported splits explicitly in source metadata instead of silently fabricating values.
- [ ] Add mocked HTTP tests for successful fetches.
- [ ] Add tests for missing split data metadata.
- [ ] Run `cd apps/api && uv run pytest`.
- [ ] Commit with `feat(ingestion): collect LG hitter stat payloads`.

## Done When

- Required LG hitter stat payloads are collected or explicitly marked unavailable.
- Later snapshot building can distinguish real splits from unavailable splits.
