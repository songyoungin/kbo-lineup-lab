# Cloud Deployment Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the repo deployable to Vercel (web) + Cloud Run (API) per `docs/superpowers/specs/2026-06-07-cloud-deployment-design.md`, then deploy.

**Architecture:** Containerize the FastAPI app with a root-context Dockerfile (uv workspace lock lives at the repo root), make CORS origins env-driven, and add an opt-in `X-API-Token` guard on the `/api/admin` and `/api/jobs` routers. The web app passes the token server-side only. Supabase and the GitHub Actions ingestion cron are untouched.

**Tech Stack:** Docker (python:3.13-slim + uv), FastAPI dependencies, Next.js env vars, gcloud CLI, Vercel.

**Conventions:** All code/comments/docstrings in English. Run API commands from `apps/api`. Lint/type-check only via pre-commit (runs on commit). Tests: `uv run pytest` from `apps/api`.

---

### Task 1: Fix Dockerfile location in the spec

The spec says `apps/api/Dockerfile` + `--source apps/api`, but `uv.lock` lives at the repo root (uv workspace with member `apps/api`), so the build context must be the repo root and `gcloud run deploy --source` requires the Dockerfile at the source root.

**Files:**
- Modify: `docs/superpowers/specs/2026-06-07-cloud-deployment-design.md`

- [ ] **Step 1: Update the spec**

In the "Code changes" section, replace item 1 with:

```markdown
1. **`Dockerfile` (repo root)** — `python:3.13-slim` base, install `uv`,
   `uv sync --frozen --no-dev` against the workspace lock (which lives at the
   repo root — this is why the Dockerfile and build context are the repo
   root, not `apps/api`), run
   `uvicorn app.main:app --host 0.0.0.0 --port $PORT` (Cloud Run injects
   `PORT`). A root `.dockerignore` keeps the context small.
```

In "One-time deployment steps", change `--source apps/api` to `--source .`.

- [ ] **Step 2: Commit**

```bash
git add docs/superpowers/specs/2026-06-07-cloud-deployment-design.md
git commit -m "docs: move Dockerfile to repo root in deployment spec (uv workspace lock)"
```

---

### Task 2: Dockerfile + .dockerignore

No TDD (infrastructure file); verified by building and running the container.

**Files:**
- Create: `Dockerfile` (repo root)
- Create: `.dockerignore` (repo root)

- [ ] **Step 1: Write `.dockerignore`**

```
.git
.venv
**/node_modules
**/.next
**/__pycache__
**/*.db
docs
postgame6-live.png
```

- [ ] **Step 2: Write `Dockerfile`**

```dockerfile
# FastAPI serving image. Build context must be the repo root: the uv
# workspace lock (uv.lock) and root pyproject.toml live there.
FROM python:3.13-slim

COPY --from=ghcr.io/astral-sh/uv:0.9 /uv /uvx /usr/local/bin/

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

WORKDIR /srv

# Install third-party deps first so this layer caches across app-code edits.
COPY pyproject.toml uv.lock ./
COPY apps/api/pyproject.toml apps/api/pyproject.toml
RUN uv sync --frozen --no-dev --no-install-workspace

# Then install the workspace package itself.
COPY apps/api apps/api
RUN uv sync --frozen --no-dev

ENV PATH="/srv/.venv/bin:$PATH"

WORKDIR /srv/apps/api

# Cloud Run injects PORT (defaults to 8080).
CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8080}"]
```

- [ ] **Step 3: Build the image**

Run from the repo root:

```bash
docker build -t kbo-lineup-lab-api .
```

Expected: build succeeds. (If Docker Desktop is not running, start it; if unavailable, note it and rely on Cloud Build at deploy time — but try locally first.)

- [ ] **Step 4: Smoke-test the container**

```bash
docker run --rm -d -p 8080:8080 --name kbo-api-smoke kbo-lineup-lab-api
sleep 3
curl -s http://localhost:8080/health
docker stop kbo-api-smoke
```

Expected: `{"status":"ok"}` (uses the bundled SQLite default — fine for a health check).

- [ ] **Step 5: Commit**

```bash
git add Dockerfile .dockerignore
git commit -m "feat(api): add Cloud Run Dockerfile with uv workspace build"
```

---

### Task 3: Env-driven CORS origins

**Files:**
- Modify: `apps/api/app/main.py`
- Test: `apps/api/tests/test_app_config.py` (new)

- [ ] **Step 1: Write the failing tests**

Create `apps/api/tests/test_app_config.py`:

```python
"""Tests for env-driven app configuration (CORS origins)."""

import pytest
from fastapi.testclient import TestClient

from app.main import app, cors_origins

client = TestClient(app)


def test_cors_origins_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """Without KBO_CORS_ORIGINS, the local dev origins are returned."""
    monkeypatch.delenv("KBO_CORS_ORIGINS", raising=False)
    assert cors_origins() == [
        "http://localhost:3000",
        "http://127.0.0.1:3000",
    ]


def test_cors_origins_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """KBO_CORS_ORIGINS is split on commas and whitespace-stripped."""
    monkeypatch.setenv(
        "KBO_CORS_ORIGINS", "https://app.vercel.app, https://other.app ,"
    )
    assert cors_origins() == ["https://app.vercel.app", "https://other.app"]


def test_cors_header_for_default_origin() -> None:
    """The middleware echoes an allowed origin (default localhost set)."""
    res = client.get("/health", headers={"Origin": "http://localhost:3000"})
    assert res.headers.get("access-control-allow-origin") == "http://localhost:3000"
```

- [ ] **Step 2: Run tests to verify they fail**

From `apps/api`:

```bash
uv run pytest tests/test_app_config.py -v
```

Expected: FAIL — `ImportError: cannot import name 'cors_origins'`.

- [ ] **Step 3: Implement `cors_origins()` in `apps/api/app/main.py`**

Replace the import block and the middleware registration (lines 1–20) with:

```python
import os

from fastapi import APIRouter, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app import models as _models  # noqa: F401 — registers all ORM models with Base.metadata
from app.api.routes import admin, games, jobs, team

# Local web dev server origins (IPv4/IPv6 variants). Client components fetch
# the API cross-origin, which the browser blocks without CORS headers.
_DEFAULT_CORS_ORIGINS = "http://localhost:3000,http://127.0.0.1:3000"


def cors_origins() -> list[str]:
    """Return browser origins allowed by CORS, from KBO_CORS_ORIGINS (comma-separated) or the local dev defaults."""
    raw = os.environ.get("KBO_CORS_ORIGINS") or _DEFAULT_CORS_ORIGINS
    return [origin.strip() for origin in raw.split(",") if origin.strip()]


app = FastAPI(title="KBO Lineup Lab API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins(),
    allow_methods=["*"],
    allow_headers=["*"],
)
```

(The rest of the file — `/health` and the routers — is unchanged.)

- [ ] **Step 4: Run tests to verify they pass**

```bash
uv run pytest tests/test_app_config.py -v
```

Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add apps/api/app/main.py apps/api/tests/test_app_config.py
git commit -m "feat(api): read CORS origins from KBO_CORS_ORIGINS env var"
```

---

### Task 4: Opt-in API token guard for admin/jobs routers

**Files:**
- Modify: `apps/api/app/api/deps.py`
- Modify: `apps/api/app/main.py`
- Test: `apps/api/tests/test_api_token_guard.py` (new)

- [ ] **Step 1: Write the failing tests**

Create `apps/api/tests/test_api_token_guard.py`:

```python
"""Tests for the opt-in X-API-Token guard on the admin/jobs routers."""

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.api.deps import require_api_token
from app.main import app

client = TestClient(app)


def test_guard_is_noop_when_env_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    """Without KBO_ADMIN_TOKEN the guard accepts any (or no) header."""
    monkeypatch.delenv("KBO_ADMIN_TOKEN", raising=False)
    require_api_token(None)
    require_api_token("anything")


def test_guard_accepts_matching_token(monkeypatch: pytest.MonkeyPatch) -> None:
    """A header equal to KBO_ADMIN_TOKEN passes."""
    monkeypatch.setenv("KBO_ADMIN_TOKEN", "sekrit")
    require_api_token("sekrit")


@pytest.mark.parametrize("sent", [None, "", "wrong"])
def test_guard_rejects_bad_token(
    monkeypatch: pytest.MonkeyPatch, sent: str | None
) -> None:
    """A missing/empty/mismatched header raises 401 when the env is set."""
    monkeypatch.setenv("KBO_ADMIN_TOKEN", "sekrit")
    with pytest.raises(HTTPException) as exc_info:
        require_api_token(sent)
    assert exc_info.value.status_code == 401


def test_admin_route_401_without_token(monkeypatch: pytest.MonkeyPatch) -> None:
    """The admin router is wired to the guard."""
    monkeypatch.setenv("KBO_ADMIN_TOKEN", "sekrit")
    assert client.get("/api/admin/ingestion-runs").status_code == 401


def test_jobs_route_401_without_token(monkeypatch: pytest.MonkeyPatch) -> None:
    """The jobs router is wired to the guard."""
    monkeypatch.setenv("KBO_ADMIN_TOKEN", "sekrit")
    assert client.post("/api/jobs/replay-evaluation", json={}).status_code == 401


def test_games_route_stays_public(monkeypatch: pytest.MonkeyPatch) -> None:
    """Read endpoints are not behind the guard even when the env is set."""
    monkeypatch.setenv("KBO_ADMIN_TOKEN", "sekrit")
    assert client.get("/api/games/999999/pregame").status_code != 401
```

- [ ] **Step 2: Run tests to verify they fail**

From `apps/api`:

```bash
uv run pytest tests/test_api_token_guard.py -v
```

Expected: FAIL — `ImportError: cannot import name 'require_api_token'`.

- [ ] **Step 3: Add the guard to `apps/api/app/api/deps.py`**

Full new file content:

```python
"""FastAPI dependency providers for shared resources."""

import os
import secrets
from collections.abc import Iterator
from typing import Annotated

from fastapi import Depends, Header, HTTPException
from sqlalchemy.orm import Session

from app.db.session import SessionLocal


def get_session() -> Iterator[Session]:
    """Yield a SQLAlchemy session, closing it on exit."""
    with SessionLocal() as session:
        yield session


SessionDep = Annotated[Session, Depends(get_session)]


def require_api_token(
    x_api_token: Annotated[str | None, Header()] = None,
) -> None:
    """Reject the request unless X-API-Token matches KBO_ADMIN_TOKEN.

    Opt-in guard: when the KBO_ADMIN_TOKEN env var is unset or empty the
    check is a no-op, so local dev and the fixture demo need no setup.

    Raises:
        HTTPException: 401 when the env var is set and the header is
            missing or does not match.
    """
    expected = os.environ.get("KBO_ADMIN_TOKEN")
    if not expected:
        return
    if x_api_token is None or not secrets.compare_digest(x_api_token, expected):
        raise HTTPException(status_code=401, detail="Invalid or missing API token")
```

- [ ] **Step 4: Wire the guard in `apps/api/app/main.py`**

Update the imports and the two router registrations:

```python
from fastapi import APIRouter, Depends, FastAPI
```

```python
from app.api.deps import require_api_token
```

```python
api_v1.include_router(
    jobs.router,
    prefix="/jobs",
    tags=["jobs"],
    dependencies=[Depends(require_api_token)],
)
api_v1.include_router(
    admin.router,
    prefix="/admin",
    tags=["admin"],
    dependencies=[Depends(require_api_token)],
)
```

(`team` and `games` registrations stay as they are.)

- [ ] **Step 5: Run tests to verify they pass**

```bash
uv run pytest tests/test_api_token_guard.py -v
```

Expected: 8 passed (3 are the parametrized rejects).

- [ ] **Step 6: Run the full API suite**

```bash
uv run pytest
```

Expected: all pass — in particular `tests/test_ingestion_status_api.py` and `tests/test_pipeline_jobs.py` must still pass because `KBO_ADMIN_TOKEN` is unset in the test environment.

- [ ] **Step 7: Commit**

```bash
git add apps/api/app/api/deps.py apps/api/app/main.py apps/api/tests/test_api_token_guard.py
git commit -m "feat(api): opt-in X-API-Token guard on admin and jobs routers"
```

---

### Task 5: Web sends the token server-side; env templates

The `/admin/ingestion` page is a server component, so the token comes from a server-only env var and never reaches the browser. (`lib/api.ts` is also imported by client components; non-`NEXT_PUBLIC_` env reads are `undefined` in the browser bundle, so nothing leaks.) No web test runner exists — verified by lint/build (Task 6) and the deploy checklist.

**Files:**
- Modify: `apps/web/lib/api.ts`
- Modify: `apps/api/.env.example`

- [ ] **Step 1: Add the admin header helper to `apps/web/lib/api.ts`**

Insert after the `ApiError` class:

```ts
/**
 * Server-only auth header for admin/jobs endpoints. KBO_ADMIN_TOKEN is not
 * NEXT_PUBLIC_, so it is undefined in the browser bundle and the header is
 * simply omitted there (and in local dev where the API guard is off).
 */
function adminHeaders(): HeadersInit {
  const token = process.env.KBO_ADMIN_TOKEN;
  return token ? { "x-api-token": token } : {};
}
```

- [ ] **Step 2: Attach the header to the two admin calls in the `api` object**

```ts
  adminIngestionRuns: (limit = 50) =>
    apiGet<IngestionRunListResponse>(
      `/api/admin/ingestion-runs?limit=${limit}`,
      { headers: adminHeaders() }
    ),

  adminGameIngestionStatus: (gameId: number) =>
    apiGet<GameIngestionStatusResponse>(
      `/api/admin/games/${gameId}/ingestion-status`,
      { headers: adminHeaders() }
    ),
```

(`apiGet` already spreads `init` into `fetch`, so no other change is needed.)

- [ ] **Step 3: Add the new env vars to `apps/api/.env.example`**

Append:

```
# Serving deployment (optional; both unset for local dev).
# When KBO_ADMIN_TOKEN is set, /api/admin/* and /api/jobs/* require a matching
# X-API-Token header. Set the same value in the web app's server-only env.
KBO_ADMIN_TOKEN=
# Comma-separated browser origins allowed by CORS. Defaults to the local dev
# origins (http://localhost:3000, http://127.0.0.1:3000) when unset.
KBO_CORS_ORIGINS=
```

- [ ] **Step 4: Commit**

```bash
git add apps/web/lib/api.ts apps/api/.env.example
git commit -m "feat(web): send X-API-Token for admin endpoints server-side"
```

---

### Task 6: Full verification + PR

- [ ] **Step 1: API suite**

From `apps/api`: `uv run pytest` — expected: all pass.

- [ ] **Step 2: Web lint/format/build**

From `apps/web`:

```bash
npm run lint && npm run format:check && npm run build
```

Expected: all pass.

- [ ] **Step 3: Container rebuild with final code**

From the repo root:

```bash
docker build -t kbo-lineup-lab-api . \
  && docker run --rm -d -p 8080:8080 -e KBO_ADMIN_TOKEN=sekrit --name kbo-api-smoke kbo-lineup-lab-api \
  && sleep 3 \
  && curl -s -o /dev/null -w "no-token: %{http_code}\n" http://localhost:8080/api/admin/ingestion-runs \
  && curl -s -o /dev/null -w "with-token: %{http_code}\n" -H "x-api-token: sekrit" http://localhost:8080/api/admin/ingestion-runs \
  && docker stop kbo-api-smoke
```

Expected: `no-token: 401`, `with-token: 200`.

- [ ] **Step 4: Harness audit + PR**

Run `/harness-audit`; then push `feature/cloud-deployment` and open the PR with `gh pr create` (account `songyoungin`). After CI passes, merge (merge commit).

---

### Task 7: Deployment (Claude executes; user does the one-time auth)

Claude runs these gcloud / Vercel CLI commands directly in the session. The user only performs the interactive auth steps: `gcloud auth login` with the personal Google account (NOT the SOCAR SSO profiles) and a Vercel login/token. Confirm each mutating command with the user before running. After a successful deploy, commit the working commands as `scripts/deploy/deploy_api.sh` and `scripts/deploy/README.md` for repeatability (no Terraform: tfstate would hold the secrets in plaintext, overkill at this scale).

- [ ] **Step 1: GCP project + APIs** (user runs `! gcloud auth login` first)

```bash
gcloud config set project <PERSONAL_PROJECT_ID>
gcloud services enable run.googleapis.com cloudbuild.googleapis.com \
  secretmanager.googleapis.com artifactregistry.googleapis.com
```

- [ ] **Step 2: Secrets**

```bash
printf '%s' '<SUPABASE_SESSION_POOLER_URL>' | gcloud secrets create kbo-database-url --data-file=-
printf '%s' "$(openssl rand -hex 32)" | gcloud secrets create kbo-admin-token --data-file=-
PROJECT_NUMBER=$(gcloud projects describe <PERSONAL_PROJECT_ID> --format='value(projectNumber)')
for s in kbo-database-url kbo-admin-token; do
  gcloud secrets add-iam-policy-binding "$s" \
    --member "serviceAccount:${PROJECT_NUMBER}-compute@developer.gserviceaccount.com" \
    --role roles/secretmanager.secretAccessor
done
```

(DB URL comes from the Supabase dashboard — Session pooler, `postgresql+psycopg://` scheme as documented in `apps/api/.env.example`. Fetch via Bitwarden if stored there.)

- [ ] **Step 3: Deploy the API** (from the repo root, on the merged main)

```bash
gcloud run deploy kbo-lineup-lab-api \
  --source . --region asia-northeast3 --allow-unauthenticated \
  --min-instances 0 --max-instances 1 --memory 512Mi \
  --set-secrets KBO_DATABASE_URL=kbo-database-url:latest,KBO_ADMIN_TOKEN=kbo-admin-token:latest
```

Verify: `curl -s <RUN_URL>/health` → `{"status":"ok"}`; tokenless `curl <RUN_URL>/api/admin/ingestion-runs` → 401.

- [ ] **Step 4: Deploy the web on Vercel** (user provides a Vercel token or runs `! vercel login`)

Via Vercel CLI from `apps/web`: `vercel link` (create project `kbo-lineup-lab`), then set Production env vars `NEXT_PUBLIC_API_BASE_URL=<RUN_URL>` and `KBO_ADMIN_TOKEN=<token from Secret Manager>` with `vercel env add`, then `vercel deploy --prod`. (Dashboard import with Root Directory `apps/web` is the fallback if CLI linking misbehaves.)

- [ ] **Step 5: Point CORS at the Vercel domain**

```bash
gcloud run services update kbo-lineup-lab-api --region asia-northeast3 \
  --set-env-vars KBO_CORS_ORIGINS=https://<app>.vercel.app
```

- [ ] **Step 6: End-to-end check**

On the Vercel URL: team home renders real data; `/games/latest/pregame` redirects to the newest game; the pregame "선수 비교" client panel loads (proves CORS); `/admin/ingestion` renders (proves the server-side token path).

- [ ] **Step 7: Commit the deploy scripts**

Write the verified commands into `scripts/deploy/deploy_api.sh` (gcloud deploy + CORS update, parameterized by project/region/service) and `scripts/deploy/README.md` (one-time setup: auth, secrets, Vercel project + env vars). Commit on a `chore/` branch and PR as usual.
