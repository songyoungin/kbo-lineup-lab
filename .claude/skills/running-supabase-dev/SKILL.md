---
name: running-supabase-dev
description: Use when running the KBO Lineup Lab app locally against the real Supabase Postgres (not the SQLite fixture) — starting the API + Next.js web in dev mode backed by the live database, viewing real ingested data (pregame/postgame/team-home), or when client-side panels (e.g. the pregame "선수 비교") render empty in dev. Covers the Supabase session-pooler KBO_DATABASE_URL and the localhost-not-127.0.0.1 access requirement.
---

# Running Local Dev Against Supabase

## Overview

Run the full stack (FastAPI + Next.js dev server) against the **real Supabase
Postgres** so the app serves live, persisted data instead of the SQLite fixture.
The app is Postgres-capable (psycopg3 driver + dialect-aware Alembic migrations),
so this only requires pointing `KBO_DATABASE_URL` at Supabase. Data shown is
whatever `kbo-lab run --date` or the ingestion canary has persisted.

Note: the `ingestion-canary` cron runs with the LLM batting-order layer ON
(`gpt-5.5`), so Supabase rows it persists carry LLM-ordered lineups and
per-player rationale (shown as the pregame comparison reason). A *local* run
using the `.env.example` defaults (LLM off) instead shows the deterministic
fallback order, so the two sources can differ for the same game.

For the SQLite/fixture path instead, use the `running-fixture-demo` skill.

**Critical gotcha:** open the web at **`http://localhost:3000`**, NOT
`http://127.0.0.1:3000`. Next.js dev blocks cross-origin dev assets/HMR from any
origin other than the host it bound to (`localhost`). Accessing via `127.0.0.1`
gets the HMR WebSocket rejected, which **breaks client-component hydration** —
the page still server-renders, but interactive client panels (e.g. the pregame
"선수 비교" dropdown) stay empty because their `useEffect` fetch never fires.

## Connection string (Supabase session pooler)

Use the **Session pooler** (Supavisor, IPv4, port **5432**), then rewrite the
scheme to psycopg:

```
postgresql+psycopg://postgres.<ref>:<password>@aws-<n>-<region>.pooler.supabase.com:5432/postgres
```

Get it from the Supabase dashboard → **Connect → Session pooler**. Why the
session pooler:
- **Direct connection** (`db.<ref>.supabase.co:5432`) is **IPv6** — unreachable
  from IPv4-only networks (GitHub runners, many local setups); fails DNS as
  "nodename nor servname provided".
- **Transaction pooler** (port 6543) does not support prepared statements →
  psycopg errors. Session mode (5432) is correct for a persistent backend.

The URL contains the DB password — keep it out of git. Save it once in a
**gitignored** `apps/api/.env` (`KBO_DATABASE_URL=...`) and load it per command
with `uv run --env-file .env <cmd>`. Commit only `apps/api/.env.example` with
placeholders. Rotate the password if it leaks.

## Quick Reference

| Service | Address | Notes |
|---------|---------|-------|
| API | http://127.0.0.1:8000 (`/docs`) | uvicorn; `KBO_DATABASE_URL` = Supabase pooler |
| Web | **http://localhost:3000** | Next dev — open via `localhost`, never `127.0.0.1` |
| DB | Supabase Postgres | session pooler, region e.g. `ap-northeast-2` |

## Launch Steps

```bash
# 0. From repo root: install deps
uv sync

# 1. Save the pooler URL once into a gitignored apps/api/.env:
#      KBO_DATABASE_URL=postgresql+psycopg://postgres.<ref>:<pw>@aws-<n>-<region>.pooler.supabase.com:5432/postgres

# 2. API against Supabase (run from apps/api), loading the .env each command
cd apps/api
uv run --env-file .env kbo-lab bootstrap   # idempotent: migrate + seed teams/ModelVersion (schema + refs only, NOT fixture data)
uv run --env-file .env uvicorn app.main:app --port 8000

# 3. (optional) ingest one day of real data if the DB has none
uv run --env-file .env kbo-lab run --date 2026-05-30   # bootstrap + live Naver ingest + eval + postgame

# 3. Web dev (separate shell). .env.local already points at :8000;
#    only set NEXT_PUBLIC_API_BASE_URL if the API runs on a different port.
cd apps/web && npm run dev
# then open http://localhost:3000   (NOT 127.0.0.1)
```

If the API is not on `:8000`, start the web with the matching base URL:
`NEXT_PUBLIC_API_BASE_URL="http://127.0.0.1:8011" npm run dev`.

## Smoke Test

```bash
curl -s http://127.0.0.1:8000/health                          # {"status":"ok"}
curl -s http://127.0.0.1:8000/api/team/lg/home                # pipeline complete/normalized when data exists
```

In the browser at **http://localhost:3000**:
- `/` team home — today's game card with a real `pipeline_status`.
- `/games/1/pregame` — change the "타순 선택" dropdown; the comparison table
  must populate (this confirms client hydration is working).
- `/games/1/postgame`, `/admin/ingestion`.

## Common Mistakes

- **Web opened via `127.0.0.1:3000` → interactive client panels are empty**
  (e.g. pregame "선수 비교" shows the dropdown but no content). Root cause: Next
  dev cross-origin block on the HMR WebSocket breaks hydration. **Fix: open
  `http://localhost:3000`.** Do NOT add `allowedDevOrigins` with a LAN IP to
  work around it — that widens the dev server's attack surface (dev-only
  endpoints reachable from other devices); `localhost` is the safest option.
  This is a Next-dev/HMR concern, NOT CORS: the API can stay on `127.0.0.1:8000`
  because its CORS list already allows both `localhost:3000` and `127.0.0.1:3000`.
- **Direct connection fails (DNS / IPv6)** — `db.<ref>.supabase.co` is IPv6.
  Use the **session pooler** host instead.
- **`prepared statement` errors** — using the transaction pooler (`:6543`).
  Switch to the session pooler (`:5432`).
- **CORS / client fetch blocked** — the API must allow the web origin;
  `app/main.py` allows `localhost:3000` and `127.0.0.1:3000`.
- **Env mismatch** — the API server and every `kbo-lab`/Python command must
  share the same `KBO_DATABASE_URL`, or they hit different databases.

## Fallback: production build (only if dev hydration cannot be used)

Prefer `localhost` access in dev. If a fully-hydrated app must be served from a
non-`localhost` host, build for production (no HMR dependency), inlining the API
URL at build time:

```bash
cd apps/web
NEXT_PUBLIC_API_BASE_URL="http://127.0.0.1:8000" npm run build
NEXT_PUBLIC_API_BASE_URL="http://127.0.0.1:8000" npm run start -- --port 3000
```

Note: production mode has no hot reload — rebuild after code changes.
