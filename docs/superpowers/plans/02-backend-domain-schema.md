# Backend Domain Schema Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Define the database schema for LG lineup evaluation, snapshots, runs, and postgame reviews.

**Architecture:** Store normalized domain data separately from raw ingestion payloads. All model outputs must reference immutable snapshots and model versions.

**Tech Stack:** Python, SQLAlchemy 2.x, Alembic, SQLite for MVP.

---

## Scope

Implement database models and the first Alembic migration. Do not implement ingestion or scoring logic.

## Files

- Create: `apps/api/app/db/base.py`
- Create: `apps/api/app/db/session.py`
- Create: `apps/api/app/models/team.py`
- Create: `apps/api/app/models/player.py`
- Create: `apps/api/app/models/game.py`
- Create: `apps/api/app/models/snapshot.py`
- Create: `apps/api/app/models/evaluation.py`
- Create: `apps/api/app/models/postgame.py`
- Create: `apps/api/alembic/env.py`
- Create: `apps/api/alembic/versions/0001_initial_schema.py`
- Modify: `apps/api/pyproject.toml`

## Steps

- [ ] Add SQLAlchemy and Alembic dependencies.
- [ ] Configure a SQLite database URL using `KBO_DATABASE_URL`, defaulting to `sqlite:///./kbo_lineup_lab.db`.
- [ ] Define tables listed in the MVP design: teams, players, games, ingestion runs, stat snapshots, lineup snapshots, model versions, evaluation runs, recommended lineup rows, box score snapshots, and postgame reviews.
- [ ] Add uniqueness for the pregame idempotency key: `game_id`, `team_id`, `evaluation_cutoff_at`, `stat_snapshot_id`, `lineup_snapshot_id`, `model_version_id`.
- [ ] Add indexes for `snapshot_at`, `announced_at`, `game_date`, and run status.
- [ ] Add tests that create an in-memory SQLite database and verify table creation.
- [ ] Run `cd apps/api && uv run pytest`.
- [ ] Commit with `feat(api): add domain database schema`.

## Done When

- Alembic migration creates the schema.
- Tests can create all metadata in SQLite.
- The idempotency uniqueness constraint exists.
