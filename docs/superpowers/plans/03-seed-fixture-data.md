# Seed Fixture Data Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add deterministic LG Twins fixture data for early model and UI development.

**Architecture:** Use local JSON fixtures as the first data source. Load them through service code so later collectors can replace the fixture source without changing model code.

**Tech Stack:** Python, Pydantic, SQLAlchemy, pytest.

---

## Scope

Seed one LG game, roster hitters, stat snapshot rows, actual lineup snapshot, and box score rows. Do not scrape live sources.

## Files

- Create: `apps/api/fixtures/lg_2026_sample.json`
- Create: `apps/api/app/schemas/fixtures.py`
- Create: `apps/api/app/services/fixture_loader.py`
- Create: `apps/api/tests/test_fixture_loader.py`
- Modify: `apps/api/pyproject.toml`

## Steps

- [ ] Create a fixture JSON file with one LG game, at least 12 LG hitters, one opponent starter, one actual lineup, one stat snapshot, and one postgame box score.
- [ ] Add Pydantic schemas validating fixture shape.
- [ ] Implement `load_fixture_file(path, session)` to upsert fixture data into the database.
- [ ] Ensure fixture load is idempotent: running it twice must not duplicate teams, players, games, snapshots, or rows.
- [ ] Add pytest coverage for first load and second load.
- [ ] Run `cd apps/api && uv run pytest`.
- [ ] Commit with `feat(api): add deterministic LG fixture loader`.

## Done When

- Fixture data can seed the database repeatedly.
- Later tasks can build model runs using the seeded game and snapshots.
