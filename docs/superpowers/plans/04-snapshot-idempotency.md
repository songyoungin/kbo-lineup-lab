# Snapshot Idempotency Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement cutoff-safe snapshot selection and deterministic evaluation run identity.

**Architecture:** Model execution must select only snapshots available at or before `evaluation_cutoff_at`. The same key must return the existing run instead of creating duplicates.

**Tech Stack:** Python, SQLAlchemy, pytest.

---

## Scope

Implement services for snapshot lookup, input manifest creation, hashing, and evaluation run creation. Do not compute lineup scores yet.

## Files

- Create: `apps/api/app/services/snapshot_selector.py`
- Create: `apps/api/app/services/run_manifest.py`
- Create: `apps/api/app/services/evaluation_runs.py`
- Create: `apps/api/tests/test_snapshot_selector.py`
- Create: `apps/api/tests/test_evaluation_run_idempotency.py`

## Steps

- [ ] Implement `select_stat_snapshot(session, team_id, cutoff_at)` choosing the latest `snapshot_at <= cutoff_at`.
- [ ] Implement `select_lineup_snapshot(session, game_id, team_id, cutoff_at)` choosing the latest `announced_at <= cutoff_at`.
- [ ] Raise a typed error when no cutoff-safe snapshot exists.
- [ ] Implement canonical JSON manifest serialization with sorted keys.
- [ ] Implement SHA-256 input hashing for manifests.
- [ ] Implement `get_or_create_evaluation_run(...)` using the idempotency key.
- [ ] Add tests proving future snapshots are ignored.
- [ ] Add tests proving duplicate run calls return the same row.
- [ ] Run `cd apps/api && uv run pytest`.
- [ ] Commit with `feat(api): add snapshot-safe evaluation runs`.

## Done When

- Past-game replay cannot accidentally use future data.
- Repeated evaluation run creation is idempotent.
