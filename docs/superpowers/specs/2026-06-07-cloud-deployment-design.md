# Cloud Deployment Design — Vercel + Cloud Run

Date: 2026-06-07
Status: approved

## Goal

Deploy KBO Lineup Lab for personal use at zero hosting cost: the Next.js web
app on Vercel (Hobby) and the FastAPI API on Google Cloud Run
(scale-to-zero). Supabase Postgres and the GitHub Actions ingestion cron stay
exactly as they are.

## Context and constraints

- Audience: owner only (personal). Cold starts of a few seconds are
  acceptable; a ~1 minute cold start (Render free tier) is not.
- Budget: free-tier hosting. Cloud Run's free quota and Vercel Hobby cover
  personal traffic; Supabase and OpenAI costs are unchanged.
- The DB is already cloud-hosted (Supabase Postgres). Ingestion already runs
  in GitHub Actions (`ingestion-canary`, 03:00 KST) and writes to Supabase
  directly, so the serving API never needs to be always-on.
- The serving API keeps both LLM layers disabled (their engine defaults).
  Canary-persisted rows already carry the LLM batting order and postgame
  narrative, so serving is read-only with respect to LLM output and the API
  container needs no `OPENAI_API_KEY`.

## Architecture

```
Browser
 ├─ https://<app>.vercel.app          Next.js 16 (Vercel Hobby)
 │    ├─ server-component fetch ────→ Cloud Run API
 │    └─ client-component fetch ────→ Cloud Run API (CORS)
 └─ https://<api>.run.app             FastAPI (Cloud Run, asia-northeast3,
                                      min 0 / max 1 instances)
                                        ↓ Supabase session pooler (IPv4, :5432)
                                      Supabase Postgres (unchanged)
                                        ↑
GitHub Actions ingestion cron ──────────┘ (unchanged)
```

Direct Supabase connections are IPv6-only; Cloud Run egress is IPv4, so the
API must use the session pooler URL (the connection string already documented
in `apps/api/.env.example`).

## Code changes (this repo)

1. **`apps/api/Dockerfile`** — `python:3.13-slim` base, install `uv`,
   `uv sync --frozen --no-dev`, run
   `uvicorn app.main:app --host 0.0.0.0 --port $PORT` (Cloud Run injects
   `PORT`).
2. **CORS from env** — replace the hardcoded origin list in
   `apps/api/app/main.py` with `KBO_CORS_ORIGINS` (comma-separated). Default
   when unset: the current two localhost origins, so local dev is unchanged.
3. **Token guard for mutating/admin routes** — a FastAPI dependency on the
   `/api/admin` and `/api/jobs` routers: requests must send an `X-API-Token`
   header equal to `KBO_ADMIN_TOKEN`. When the env var is unset the guard is
   a no-op (local dev and the fixture demo keep working without setup).
   Mismatch or missing header with the env set → 401. Game/team read
   endpoints stay public.
4. **Web passes the token server-side** — `/admin/ingestion` is a server
   component; its API calls attach `X-API-Token` from a server-only
   `KBO_ADMIN_TOKEN` env var (not `NEXT_PUBLIC_`), so the token never reaches
   the browser. No web code calls the `/api/jobs` POST endpoints.
5. **Env templates and harness** — add `KBO_CORS_ORIGINS` and
   `KBO_ADMIN_TOKEN` to `apps/api/.env.example`; update `CLAUDE.md` /
   affected skills if any documented behavior changes.

## One-time deployment steps (outside the repo)

- **Cloud Run**: create/choose a GCP project, then
  `gcloud run deploy kbo-lineup-lab-api --source apps/api
  --region asia-northeast3 --allow-unauthenticated --min-instances 0
  --max-instances 1`. Env: `KBO_DATABASE_URL` and `KBO_ADMIN_TOKEN` from
  Secret Manager, `KBO_CORS_ORIGINS=https://<app>.vercel.app`.
- **Vercel**: import the GitHub repo (songyoungin account), Root Directory
  `apps/web`. Env: `NEXT_PUBLIC_API_BASE_URL=<Cloud Run URL>`,
  `KBO_ADMIN_TOKEN=<same secret>`.
- **Migrations**: already applied to Supabase; future migrations stay a
  manual local `uv run alembic upgrade head` (deliberate YAGNI for a
  personal deployment).
- **Deploy automation**: first deploys are manual. A GitHub Actions
  deploy-on-push workflow is a separate follow-up, not in scope.

## Edge cases

- Cold start (2–5 s on the first request after idle): accepted trade-off.
- API unreachable: the home page falls back to mock data with a notice;
  detail pages render their existing `error.tsx`. Unchanged behavior.
- Token guard precedence: env unset → open (dev); env set → enforced
  everywhere, including local runs that opt in.

## Verification

- Local: `docker build` + run the container; `/health` returns 200; with
  `KBO_ADMIN_TOKEN` set, admin/jobs return 401 without the header and 200
  with it; CORS headers reflect `KBO_CORS_ORIGINS`. Unit tests cover the
  token dependency (set/unset × header present/absent/mismatched).
- Post-deploy: team home renders on the Vercel URL, `/games/latest/*`
  redirects work, the admin page renders (token path), and a tokenless
  `curl` against `/api/admin/ingestion-runs` returns 401.

## Cost

KRW 0/month for hosting (Cloud Run free quota, Vercel Hobby). Supabase and
OpenAI spend unchanged.
