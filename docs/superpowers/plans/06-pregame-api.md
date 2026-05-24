# Pregame API Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Expose backend APIs for team home, pregame evaluation, lineup comparison, and player comparison.

**Architecture:** API routes should return view-ready JSON assembled by service layer functions. Route handlers should stay thin.

**Tech Stack:** FastAPI, Pydantic, SQLAlchemy, pytest.

---

## Scope

Expose read APIs and one job endpoint for replay/evaluation. Do not build frontend pages in this task.

## Files

- Create: `apps/api/app/api/deps.py`
- Create: `apps/api/app/api/routes/team.py`
- Create: `apps/api/app/api/routes/games.py`
- Create: `apps/api/app/api/routes/jobs.py`
- Create: `apps/api/app/schemas/pregame.py`
- Create: `apps/api/app/services/pregame_views.py`
- Modify: `apps/api/app/main.py`
- Create: `apps/api/tests/test_pregame_api.py`

## Steps

- [ ] Add database session dependency.
- [ ] Add `GET /api/team/lg/home`.
- [ ] Add `GET /api/games/{game_id}/pregame`.
- [ ] Add `GET /api/games/{game_id}/lineup-comparison`.
- [ ] Add `GET /api/games/{game_id}/players/compare`.
- [ ] Add `POST /api/jobs/replay-evaluation` accepting `game_id`, `team_id`, `evaluation_cutoff_at`, and `model_version`.
- [ ] Add tests using fixture data and `TestClient`.
- [ ] Ensure replay endpoint returns the same evaluation run id on repeated calls.
- [ ] Run `cd apps/api && uv run pytest`.
- [ ] Commit with `feat(api): expose pregame evaluation endpoints`.

## Done When

- API can return the full pregame experience from fixture data.
- Replay endpoint proves idempotency through HTTP tests.
