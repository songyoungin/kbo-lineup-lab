# Schedule And Roster Collector Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Collect LG Twins schedule and roster data from selected KBO data sources.

**Architecture:** Collectors fetch raw source data only. Parsing into domain tables belongs to the normalizer task.

**Tech Stack:** Python, httpx, Pydantic, pytest.

---

## Scope

Implement schedule and roster collectors for the selected sources from `docs/data-sources/kbo-source-matrix.md`.

## Files

- Create: `apps/api/app/ingestion/http_client.py`
- Create: `apps/api/app/ingestion/collectors/schedule.py`
- Create: `apps/api/app/ingestion/collectors/roster.py`
- Create: `apps/api/tests/test_schedule_roster_collectors.py`
- Modify: `apps/api/pyproject.toml`

## Steps

- [ ] Add `httpx` dependency.
- [ ] Implement timeout, user-agent, retry, and response-size limits in `http_client.py`.
- [ ] Implement `collect_lg_schedule(date_from, date_to)` returning raw payload metadata.
- [ ] Implement `collect_lg_roster(season)` returning raw payload metadata.
- [ ] Save all fetched responses through the raw ingestion store.
- [ ] Add tests using mocked HTTP responses.
- [ ] Add a test proving only LG-relevant schedule entries are requested or retained for downstream parsing.
- [ ] Run `cd apps/api && uv run pytest`.
- [ ] Commit with `feat(ingestion): collect LG schedule and roster payloads`.

## Done When

- Schedule and roster raw payloads can be fetched and stored.
- Tests do not require network access.
