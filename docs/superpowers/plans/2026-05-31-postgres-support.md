# Postgres Support (Supabase persistence) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the app run on Postgres (not just SQLite) so a `KBO_DATABASE_URL` pointing at a managed Postgres (Supabase session pooler) lets the ingestion canary, CLI, and API/web persist and serve real data.

**Architecture:** The app is dialect-agnostic except for two SQLite assumptions: (1) no Postgres driver is installed, and (2) Alembic `env.py` sets `render_as_batch=True` unconditionally (a SQLite ALTER workaround). Add `psycopg[binary]` (psycopg3, used via `postgresql+psycopg://`) and make `render_as_batch` apply only to the SQLite dialect. The SQLite `connect_args` guards in `db/session.py` and `jobs/bootstrap.py` already no-op on non-SQLite URLs. Verify the whole chain on a real Postgres locally via Docker (`kbo-lab bootstrap` + `kbo-lab run`). No app logic changes; the canary workflow already consumes `KBO_DATABASE_URL` and needs no edit.

**Tech Stack:** Python 3.13, SQLAlchemy 2.x, Alembic, psycopg3, uv, Docker (local Postgres for verification), Typer CLI (`kbo-lab`). English commits/docstrings/comments. Pre-commit hooks must pass.

---

## Background

Verified facts:
- `apps/api/pyproject.toml` dependencies: `alembic, fastapi, httpx, pydantic, sqlalchemy, typer, uvicorn` — **no Postgres driver**.
- `apps/api/app/db/session.py:18,22`: `_is_sqlite = DATABASE_URL.startswith("sqlite")`; `connect_args={"check_same_thread": False} if _is_sqlite else {}` — already PG-safe.
- `apps/api/app/jobs/bootstrap.py:116`: same SQLite guard — PG-safe.
- `apps/api/alembic/env.py:43` (offline) and `:62` (online): `render_as_batch=True` unconditional. The migrations (`alembic/versions/0001..0005`) use `op.batch_alter_table(...)` blocks; on Postgres these run as native ALTER (Alembic `recreate='auto'` never recreates on PG — no `recreate='always'` present), so they are expected to apply, but `render_as_batch` should be SQLite-only as correct hygiene.
- The ingestion canary (`.github/workflows/ingestion-canary.yml`) already reads `KBO_DATABASE_URL` (secret or throwaway sqlite); once `psycopg` is installed and the secret is a Postgres URL, it works unchanged.

**Supabase connection (for the eventual secret — not set in this PR):** GitHub Actions runners are IPv4-only; Supabase **direct** connection is IPv6. Use the **Session pooler** (Supavisor, IPv4, port 5432, supports migrations/prepared statements) connection string, rewritten to the SQLAlchemy+psycopg scheme:
`postgresql+psycopg://postgres.<ref>:<password>@aws-<region>.pooler.supabase.com:5432/postgres`.
(Transaction pooler on 6543 does not support prepared statements — do not use it for this app.)

## File Structure

- **Modify** `apps/api/pyproject.toml` — add `psycopg[binary]` to dependencies (via `uv add`, which also updates `uv.lock`).
- **Modify** `apps/api/alembic/env.py` — make `render_as_batch` conditional on the SQLite dialect (offline + online).

No other files change. (`db/session.py`/`bootstrap.py` guards already handle non-SQLite; the canary workflow already consumes the env var.)

---

### Task 1: Add Postgres driver + dialect-aware Alembic batch mode

**Files:**
- Modify: `apps/api/pyproject.toml` (+ `uv.lock` via uv)
- Modify: `apps/api/alembic/env.py` (lines ~36-50 offline, ~55-66 online)

- [ ] **Step 1: Add the Postgres driver**

Run: `cd /Users/serena/Documents/kbo-lineup-lab/apps/api && uv add "psycopg[binary]"`
Expected: `psycopg` added to `[project.dependencies]` in `apps/api/pyproject.toml`, `uv.lock` updated, install succeeds. (psycopg3 binary build — no system libpq needed; used by SQLAlchemy via the `postgresql+psycopg://` scheme.)

- [ ] **Step 2: Make `render_as_batch` SQLite-only in `env.py`**

In `apps/api/alembic/env.py`, the offline `run_migrations_offline()` currently has:

```python
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=True,  # required for SQLite ALTER support
    )
```

Change the `render_as_batch` line to:

```python
        render_as_batch=url.startswith("sqlite"),  # batch ALTER is a SQLite-only workaround
```

And the online `run_migrations_online()` currently has:

```python
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            render_as_batch=True,  # required for SQLite ALTER support
        )
```

Change the `render_as_batch` line to:

```python
            render_as_batch=connection.dialect.name == "sqlite",  # SQLite-only workaround
```

- [ ] **Step 3: Confirm the SQLite suite still passes (no regression)**

Run: `cd /Users/serena/Documents/kbo-lineup-lab/apps/api && uv run pytest -q`
Expected: all tests PASS (the suite uses in-memory SQLite; `render_as_batch` is still `True` for SQLite, and the new driver is inert for SQLite URLs).

- [ ] **Step 4: Verify migrations + seed apply on a REAL Postgres (Docker)**

Start a throwaway Postgres, run the bootstrap against it, and confirm tables + seed land. Run:

```bash
docker run -d --rm --name kbo_pg_verify -e POSTGRES_PASSWORD=postgres -e POSTGRES_DB=kbo -p 55432:5432 postgres:16
# wait for readiness
for i in $(seq 1 30); do docker exec kbo_pg_verify pg_isready -U postgres >/dev/null 2>&1 && break; sleep 1; done
cd /Users/serena/Documents/kbo-lineup-lab/apps/api && \
  KBO_DATABASE_URL="postgresql+psycopg://postgres:postgres@127.0.0.1:55432/kbo" uv run kbo-lab bootstrap  # pragma: allowlist secret
```

Expected: Alembic prints the 5 migrations applying, then `bootstrap: schema migrated; teams created=10; model_version_id=1`. Then verify idempotency + a table:

```bash
KBO_DATABASE_URL="postgresql+psycopg://postgres:postgres@127.0.0.1:55432/kbo" uv run --directory /Users/serena/Documents/kbo-lineup-lab/apps/api kbo-lab bootstrap  # pragma: allowlist secret
docker exec kbo_pg_verify psql -U postgres -d kbo -tAc "select count(*) from teams"
```

Expected: second bootstrap prints `teams created=0` (idempotent); the psql query prints `10`.

Leave the container running for Task 2's live smoke; it is removed in Task 2 Step 2. (If Docker is unavailable in the environment, STOP and report — this verification is the core of the task.)

- [ ] **Step 5: Commit**

```bash
cd /Users/serena/Documents/kbo-lineup-lab && git add apps/api/pyproject.toml apps/api/uv.lock apps/api/alembic/env.py && git commit -m "feat(db): add Postgres driver and dialect-aware Alembic batch mode

- add psycopg[binary] so KBO_DATABASE_URL can point at Postgres (e.g. Supabase)
- render_as_batch is now SQLite-only (it is a SQLite ALTER workaround); Postgres
  migrations run as native ALTER
- verified bootstrap (migrations + idempotent seed) against a real Postgres"
```

---

### Task 2: Full-chain live smoke on Postgres + verification

**Files:** none (verification only)

- [ ] **Step 1: Run the full pipeline against the Docker Postgres (live Naver)**

Using the `kbo_pg_verify` container from Task 1 (requires live network to api-gw.sports.naver.com):

```bash
cd /Users/serena/Documents/kbo-lineup-lab/apps/api && \
  KBO_DATABASE_URL="postgresql+psycopg://postgres:postgres@127.0.0.1:55432/kbo" uv run kbo-lab run --date 2026-05-30  # pragma: allowlist secret
echo "exit=$?"
```

Expected: `run 2026-05-30: daily=completed, game_id=1, eval_run=1, postgame_run=1` and `exit=0` — proving the entire chain (migrations + ingest + evaluation + postgame review) works on Postgres. Then spot-check persisted rows:

```bash
docker exec kbo_pg_verify psql -U postgres -d kbo -tAc "select count(*) from actual_lineup_snapshots; select count(*) from postgame_review_runs"
```

Expected: non-zero counts (a lineup snapshot and a completed postgame review persisted in Postgres).

- [ ] **Step 2: Tear down the verification container**

Run: `docker stop kbo_pg_verify`
Expected: container stops and is auto-removed (started with `--rm`). Confirm: `docker ps -a --filter name=kbo_pg_verify --format '{{.Names}}'` prints nothing.

- [ ] **Step 3: Confirm clean tree + SQLite suite**

Run: `cd /Users/serena/Documents/kbo-lineup-lab && git status -s && cd apps/api && uv run pytest -q`
Expected: working tree clean (no stray files); all tests pass.

> Note: CI continues to run the suite on SQLite (fast). Postgres compatibility is proven here via the local Docker smoke. A dedicated CI Postgres job is a possible future hardening step, out of scope for this cycle.

---

## Post-merge (requires the user; tracked separately, not part of this PR)

1. **Create the Supabase project** (or via the authenticated Supabase MCP) — confirm org + region (Seoul/ap-northeast preferred) before creating.
2. **Grab the Session pooler connection string** and rewrite the scheme to `postgresql+psycopg://...pooler.supabase.com:5432/postgres`.
3. **Set the GitHub secret**: `gh secret set KBO_DATABASE_URL --body "<pooler-url>"` (value not printed).
4. **Verify the canary persists**: `gh workflow run ingestion-canary.yml -f date=2026-05-30`, then check the run is green and the Supabase tables are populated.
5. (Security) Keep these `public` tables off the Supabase Data API, or enable RLS as defense-in-depth, since the app connects via the direct Postgres role rather than the Data API.

---

## Self-Review

**Spec coverage:**
- Run on Postgres → Task 1 (driver + dialect-aware batch). ✓
- Don't break SQLite → Task 1 Step 3 + Task 2 Step 3 (suite stays on in-memory SQLite, `render_as_batch` still True there). ✓
- Prove it actually works on PG → Task 1 Step 4 (bootstrap) + Task 2 Step 1 (full `kbo-lab run` chain) against Docker Postgres. ✓
- Canary persistence enablement → no workflow change needed (already reads `KBO_DATABASE_URL`); post-merge secret steps documented. ✓

**Placeholder scan:** No TBD/TODO/"handle edge cases". Exact `uv add`, exact env.py line replacements, exact Docker + psql verification commands with expected output. The "post-merge" section is explicitly out-of-PR (needs user credentials), not a skipped requirement.

**Type consistency:** No new Python types. The driver scheme `postgresql+psycopg://` matches the added `psycopg[binary]` (psycopg3) package. `render_as_batch` expressions evaluate to `bool` in both env.py modes (offline: `url.startswith("sqlite")`; online: `connection.dialect.name == "sqlite"`). The verification uses the existing `kbo-lab bootstrap` / `kbo-lab run --date` commands (from PRs #31/#32) unchanged.
