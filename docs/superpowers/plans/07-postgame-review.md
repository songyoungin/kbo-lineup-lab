# Postgame Review Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Generate postgame review data from pregame evaluation runs and box score snapshots.

**Architecture:** Postgame grading references the exact pregame evaluation run. It should not recompute pregame inputs using newer data.

**Tech Stack:** Python, FastAPI, SQLAlchemy, pytest.

---

## Scope

Implement deterministic postgame grading and expose read/job APIs. Do not use LLM summaries in MVP.

## Files

- Create: `apps/api/app/postgame/types.py`
- Create: `apps/api/app/postgame/performance_score.py`
- Create: `apps/api/app/postgame/review_generator.py`
- Create: `apps/api/app/services/postgame_reviews.py`
- Create: `apps/api/app/schemas/postgame.py`
- Modify: `apps/api/app/api/routes/games.py`
- Modify: `apps/api/app/api/routes/jobs.py`
- Create: `apps/api/tests/test_postgame_review.py`
- Create: `apps/api/tests/test_postgame_api.py`

## Steps

- [ ] Implement box score performance score: single, double, triple, homer, walk/HBP, run, RBI, strikeout, and GIDP weights.
- [ ] Compare actual selected players against pregame expectations.
- [ ] Classify overperformers and underperformers.
- [ ] Review choices where actual lineup differed from recommendation.
- [ ] Generate rule-based English summary text.
- [ ] Add `POST /api/jobs/generate-postgame-review`.
- [ ] Add `GET /api/games/{game_id}/postgame`.
- [ ] Add tests proving the postgame review references the original pregame evaluation run.
- [ ] Run `cd apps/api && uv run pytest`.
- [ ] Commit with `feat(api): add postgame review generation`.

## Done When

- Fixture data produces a postgame review.
- API returns overperformers, underperformers, difference reviews, and summary text.
