# Scheduled Pipeline Runner Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add repeatable job commands for the LG daily ingestion and evaluation pipeline.

**Architecture:** Use a Python CLI for MVP scheduling. The CLI can later be called by cron, GitHub Actions, or a worker process.

**Tech Stack:** Python, Typer, SQLAlchemy, pytest.

---

## Scope

Create job orchestration commands. Do not add production deployment infrastructure.

## Files

- Create: `apps/api/app/cli.py`
- Create: `apps/api/app/jobs/daily_pipeline.py`
- Create: `apps/api/app/jobs/pregame_pipeline.py`
- Create: `apps/api/app/jobs/postgame_pipeline.py`
- Create: `apps/api/tests/test_pipeline_jobs.py`
- Modify: `apps/api/pyproject.toml`

## Steps

- [ ] Add Typer dependency and CLI entrypoint.
- [ ] Implement `kbo-lab ingest-daily --date YYYY-MM-DD` for schedule, roster, and player stats.
- [ ] Implement `kbo-lab ingest-pregame --game-id GAME_ID` for lineup collection, normalization, and pregame evaluation.
- [ ] Implement `kbo-lab ingest-postgame --game-id GAME_ID` for box score collection, normalization, and postgame review.
- [ ] Record each command as an `ingestion_run` with status, started time, ended time, and error message.
- [ ] Make each command idempotent for repeated runs.
- [ ] Add tests using mocked collectors and fixture normalizers.
- [ ] Run `cd apps/api && uv run pytest`.
- [ ] Commit with `feat(jobs): add LG ingestion pipeline commands`.

## Done When

- The full LG pipeline can be run by CLI commands.
- Failed and successful runs are visible in ingestion run records.
