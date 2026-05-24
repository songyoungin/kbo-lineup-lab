# Box Score Collector Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Collect LG Twins hitter box score payloads after games finish.

**Architecture:** Box score collection powers postgame review. It should wait for final game status before generating review inputs.

**Tech Stack:** Python, httpx, pytest.

---

## Scope

Fetch raw box score payloads and expose completion status. Do not normalize rows in this task.

## Files

- Create: `apps/api/app/ingestion/collectors/box_score.py`
- Create: `apps/api/tests/test_box_score_collector.py`
- Modify: `docs/data-sources/kbo-source-matrix.md`

## Steps

- [ ] Implement `collect_lg_box_score(game_id)`.
- [ ] Store fetched payloads through the raw ingestion store.
- [ ] Return `waiting` when the game is not final.
- [ ] Return `collected` when hitter box score data is available.
- [ ] Capture final score if included in the source payload.
- [ ] Add mocked tests for non-final and final states.
- [ ] Add tests proving duplicate final payloads are deduplicated.
- [ ] Run `cd apps/api && uv run pytest`.
- [ ] Commit with `feat(ingestion): collect LG box score payloads`.

## Done When

- The pipeline can collect postgame source data only when the game is final.
- Postgame generation has a reliable collected/waiting signal.
