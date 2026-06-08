# KBO Lineup Lab — Project Guide

## Layout
Monorepo. `apps/api` is the FastAPI backend (Python 3.13, managed with `uv`); `apps/web` is the Next.js 16 frontend. Run API commands from `apps/api`.

## Running locally
- Fixtures (SQLite sample data): follow the running-fixture-demo skill.
- Real Supabase Postgres: follow the running-supabase-dev skill.
- CLI: `kbo-lab` (`apps/api/app/cli.py`) — subcommands `bootstrap`, `run`, `ingest-daily`, `ingest-pregame`, `ingest-postgame`. Invoke via `uv run kbo-lab ingest-daily` (etc.) from `apps/api`.

## Deploying
Web → Vercel (`apps/web`), API → Cloud Run. The root `Dockerfile` builds the serving image; its build context is the repo root because the `uv` workspace lock lives there, and it runs as a non-root user listening on `$PORT`. Design and step-by-step plan: `docs/superpowers/specs/` and `docs/superpowers/plans/`.

## Conventions
- English for code, docs, comments, and commit messages.
- Commit messages follow commitizen; branches use `feature/`, `fix/`, `chore/`, `refactor/`, `docs/`.
- Never use the git per-repo path flag; run git from the working directory.
- Secrets come from env / `.env` (gitignored), never hardcoded. Templates live in `apps/api/.env.example` (e.g. `LINEUP_LLM_ENABLED`, `OPENAI_API_KEY`, plus deployment vars `KBO_ADMIN_TOKEN` and `KBO_CORS_ORIGINS`).

## Quality
- Tests: `uv run pytest` from `apps/api`.
- Lint and type-check ONLY through pre-commit — never invoke the formatters/linters directly. Pre-commit runs: ruff, mypy, bandit, vulture.
- CI workflows: test, pre-commit, ingestion-canary, harness.

## Architecture invariant
Deterministic scoring and defensive position assignment in `apps/api/app/lineup_model/` are the source of truth and must stay deterministic. The LLM batting-order layer in `apps/api/app/lineup_model/batting_order/` is additive: the engine default is OFF (`build_provider()` returns None unless `LINEUP_LLM_ENABLED=true` and `OPENAI_API_KEY` are set), and on any provider failure the orderer falls back to the deterministic order. The default model is `gpt-4.1` (`provider.py` `_DEFAULT_MODEL`), for which `temperature=0` is pinned; reasoning models (`gpt-5` / `o`-series) omit `temperature` because they reject it. The deterministic scoring/position layer keeps a stable `output_hash`; once the LLM order is enabled the *recommended-lineup* `output_hash` becomes non-deterministic (best-effort only), so it is an audit fingerprint, not an idempotency key. Exception to "OFF by default": the `ingestion-canary` cron enables the layer with `gpt-5.5`, so canary-persisted recommendations carry the LLM batting order and its per-player rationale (surfaced as the pregame comparison `main_reason`). A second best-effort LLM layer produces the postgame storytelling narrative (`apps/api/app/postgame/narrative/`, gated by `POSTGAME_NARRATIVE_ENABLED`, default OFF, `gpt-5.5`); like the batting order it falls back deterministically (a Korean skeleton) and is excluded from the postgame `output_hash`. The `ingestion-canary` cron also enables it, so canary-persisted reviews carry the LLM narrative.

The `/api/admin/*` and `/api/jobs/*` routers carry an opt-in `X-API-Token` guard (`require_api_token` in `apps/api/app/api/deps.py`): it is a no-op when `KBO_ADMIN_TOKEN` is unset (so local dev, fixtures, and the read-only curls in the run skills work unchanged) and requires a matching header once the token is set in a deployed context. `CORS` origins come from `KBO_CORS_ORIGINS` (defaults to the two localhost dev origins). The web app sends the token server-side via `adminHeaders()` in `apps/web/lib/api.ts`.

## Harness discipline
This repo ships a Claude Code harness: this `CLAUDE.md`, plus agents (`.claude/agents/`), commands (`.claude/commands/`), a hook (`.claude/hooks/pretooluse_bash.py`), and drift tooling (`.claude/harness/check_drift.py`). When you change paths, commands, versions, or architecture, update the harness to match. Structural drift is enforced by `.claude/harness/check_drift.py` (pre-commit and the harness CI workflow); semantic drift is checked by the `/harness-audit` command, which also gates pull-request creation.
