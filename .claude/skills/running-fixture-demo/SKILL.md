---
name: running-fixture-demo
description: Use when launching the KBO Lineup Lab app locally to see it working — running, starting, or smoke-testing the API and web against fixture (sample) data, seeding the demo database, or demoing pregame/postgame/admin pages without real KBO ingestion.
---

# Running the Fixture Demo

## Overview

Launch the full KBO Lineup Lab stack (FastAPI + Next.js) against the
deterministic `lg_2026_sample.json` fixture. Real KBO ingestion is not yet
wired up (source URLs unverified), so this fixture path is how you see the app
end-to-end. The fixture seeds only raw snapshots — pregame evaluation and
postgame review are produced by jobs, which `scripts/seed_demo.py` runs for you.

All Python commands run from `apps/api`. The server and every Python command
**must share the same `KBO_DATABASE_URL`** (alembic's `env.py` falls back to an
invalid ini placeholder if it is unset).

## Quick Reference

| Service | Address | Notes |
|---------|---------|-------|
| API | http://127.0.0.1:8000 (`/docs`) | uvicorn |
| Web | http://localhost:3000 | Next.js; defaults to API at `:8000` |
| DB | `apps/api/kbo_lineup_lab.db` | SQLite (default URL) |

Demo game after seeding: `game_id=1` (LG vs DOO, 2026-04-15).

## Launch Steps

```bash
# 0. From repo root: install deps
uv sync

# 1. API DB + seed (run from apps/api, with an explicit DB URL)
cd apps/api
export KBO_DATABASE_URL="sqlite:///./kbo_lineup_lab.db"
uv run alembic upgrade head          # create schema (18 tables)
uv run python scripts/seed_demo.py   # fixture + ModelVersion + eval + postgame (idempotent)

# 2. Start API (same env, background)
uv run uvicorn app.main:app --host 127.0.0.1 --port 8000

# 3. Start web (separate shell)
cd apps/web && npm run dev
```

`scripts/seed_demo.py` is idempotent — re-running reports everything as skipped
/ `created=False`. Re-seeding an existing DB is safe.

## Smoke Test

```bash
curl -s http://127.0.0.1:8000/health                       # {"status":"ok"}
curl -s http://127.0.0.1:8000/api/team/lg/home             # today's game + pipeline_status
curl -s http://127.0.0.1:8000/api/games/1/pregame          # actual vs recommended lineup + verdict
curl -s http://127.0.0.1:8000/api/games/1/postgame         # over/under-performers
curl -s http://127.0.0.1:8000/api/admin/ingestion-runs     # fixture:lg_2026_sample run
```

Web pages to open: `/`, `/games/1/pregame`, `/games/1/postgame`,
`/admin/ingestion`.

## Common Mistakes

- **`NoSuchModuleError: Can't load plugin: sqlalchemy.dialects:driver`** —
  `KBO_DATABASE_URL` was not exported before `alembic`. Set it (step 1).
- **`No completed evaluation run for game_id=...`** on the pregame/comparison
  endpoints — the eval job has not run. `seed_demo.py` runs it; if you seeded
  manually, trigger `POST /api/jobs/replay-evaluation`.
- **`No lineup snapshot at-or-before cutoff`** — the evaluation cutoff is
  earlier than the lineup's `announced_at` (08:30 UTC in the fixture).
  `seed_demo.py` derives the cutoff from the snapshot, so prefer it over a
  hand-picked time.
- **Web shows nothing / fetch errors** — API not on `:8000`, or started with a
  different `KBO_DATABASE_URL` than the one you seeded. Use one URL everywhere.
