# Ingestion Normalizer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Convert raw KBO payloads into validated domain tables and snapshots.

**Architecture:** Normalizers read immutable raw payloads and produce domain rows. Player/team matching must be explicit and auditable.

**Tech Stack:** Python, Pydantic, SQLAlchemy, pytest.

---

## Scope

Normalize schedule, roster, player stats, actual lineups, and box scores. Do not implement new collectors.

## Files

- Create: `apps/api/app/ingestion/normalizers/schedule.py`
- Create: `apps/api/app/ingestion/normalizers/roster.py`
- Create: `apps/api/app/ingestion/normalizers/player_stats.py`
- Create: `apps/api/app/ingestion/normalizers/lineup.py`
- Create: `apps/api/app/ingestion/normalizers/box_score.py`
- Create: `apps/api/app/ingestion/player_matcher.py`
- Create: `apps/api/tests/test_ingestion_normalizers.py`

## Steps

- [ ] Implement schedule normalization into `games`.
- [ ] Implement roster normalization into `players`.
- [ ] Implement player stat normalization into `stat_snapshots` and `player_stat_snapshot_rows`.
- [ ] Implement lineup normalization into `actual_lineup_snapshots` and rows.
- [ ] Implement box score normalization into `box_score_snapshots` and rows.
- [ ] Implement player matching by stable source id first, then guarded name/team fallback.
- [ ] Mark ambiguous player matches as `needs_review`.
- [ ] Add tests using saved sample payloads.
- [ ] Add tests proving normalized snapshots keep source raw payload references.
- [ ] Run `cd apps/api && uv run pytest`.
- [ ] Commit with `feat(ingestion): normalize KBO payloads into snapshots`.

## Done When

- Raw KBO payloads can populate the domain tables used by model jobs.
- Ambiguous player matches are visible instead of guessed.
