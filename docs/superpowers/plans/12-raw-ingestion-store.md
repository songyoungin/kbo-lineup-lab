# Raw Ingestion Store Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Store raw KBO source payloads before parsing so failed parsers can be replayed.

**Architecture:** Collectors write immutable raw payload rows. Normalizers read from raw payloads and produce validated domain tables.

**Tech Stack:** Python, SQLAlchemy, Pydantic, pytest.

---

## Scope

Implement raw ingestion persistence and retrieval. Do not implement source-specific collectors.

## Files

- Create: `apps/api/app/ingestion/types.py`
- Create: `apps/api/app/ingestion/raw_store.py`
- Create: `apps/api/app/schemas/ingestion.py`
- Create: `apps/api/tests/test_raw_ingestion_store.py`
- Modify: `apps/api/app/models/snapshot.py`

## Steps

- [ ] Define raw payload categories: `schedule`, `roster`, `player_stats`, `lineup`, `box_score`.
- [ ] Store source name, source URL, fetched timestamp, payload hash, content type, and raw body.
- [ ] Enforce uniqueness on `source_name + source_url + payload_hash`.
- [ ] Implement `save_raw_payload(session, payload)` returning the existing row for duplicates.
- [ ] Implement `get_raw_payload(session, raw_payload_id)`.
- [ ] Add tests for duplicate payload idempotency.
- [ ] Add tests for preserving raw body content exactly.
- [ ] Run `cd apps/api && uv run pytest`.
- [ ] Commit with `feat(ingestion): add raw payload store`.

## Done When

- Raw KBO source payloads can be saved once and replayed later.
- Duplicate fetches do not create duplicate raw rows.
