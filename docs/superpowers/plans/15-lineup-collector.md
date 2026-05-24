# Lineup Collector Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Collect actual LG Twins starting lineup payloads after lineup announcement.

**Architecture:** Lineup collection must preserve announcement time and source revision so pregame replay can select cutoff-safe lineup snapshots.

**Tech Stack:** Python, httpx, pytest.

---

## Scope

Fetch and store raw lineup payloads. Do not normalize lineup rows in this task.

## Files

- Create: `apps/api/app/ingestion/collectors/lineup.py`
- Create: `apps/api/tests/test_lineup_collector.py`
- Modify: `docs/data-sources/kbo-source-matrix.md`

## Steps

- [ ] Implement `collect_lg_lineup(game_id)` for a selected KBO lineup source.
- [ ] Store fetched payloads through the raw ingestion store.
- [ ] Capture source fetch time and, when available, official lineup announcement time.
- [ ] Return a status of `waiting` when the lineup is not announced yet.
- [ ] Return a status of `collected` when lineup data is available.
- [ ] Add mocked tests for waiting and collected states.
- [ ] Add tests proving repeated identical lineup payloads are deduplicated.
- [ ] Run `cd apps/api && uv run pytest`.
- [ ] Commit with `feat(ingestion): collect LG lineup payloads`.

## Done When

- The pipeline can poll for LG lineups without creating duplicate raw records.
- Lineup availability is explicit.
