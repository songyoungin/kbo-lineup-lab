# KBO Lineup Lab — Project Guide

## Layout
Monorepo. `apps/api` is the FastAPI backend (Python 3.13, managed with `uv`); `apps/web` is the Next.js 16 frontend. Run API commands from `apps/api`.

## Running locally
- Fixtures (SQLite sample data): follow the running-fixture-demo skill.
- Real Supabase Postgres: follow the running-supabase-dev skill.
- CLI: `kbo-lab` (`apps/api/app/cli.py`) — subcommands `bootstrap`, `run`, `ingest-daily`, `ingest-pregame`, `ingest-postgame`. Invoke via `uv run kbo-lab ingest-daily` (etc.) from `apps/api`.

## Conventions
- English for code, docs, comments, and commit messages.
- Commit messages follow commitizen; branches use `feature/`, `fix/`, `chore/`, `refactor/`, `docs/`.
- Never use the git per-repo path flag; run git from the working directory.
- Secrets come from env / `.env` (gitignored), never hardcoded. Templates live in `apps/api/.env.example` (e.g. `LINEUP_LLM_ENABLED`, `OPENAI_API_KEY`).

## Quality
- Tests: `uv run pytest` from `apps/api`.
- Lint and type-check ONLY through pre-commit — never invoke the formatters/linters directly. Pre-commit runs: ruff, mypy, bandit, vulture.
- CI workflows: test, pre-commit, ingestion-canary, harness.

## Architecture invariant
Deterministic scoring and defensive position assignment in `apps/api/app/lineup_model/` are the source of truth and must stay deterministic (stable `output_hash`). The LLM batting-order layer in `apps/api/app/lineup_model/batting_order/` is additive and OFF by default (`LINEUP_LLM_ENABLED`); on any failure it falls back to the deterministic order.

## Harness discipline
This repo ships a Claude Code harness: this `CLAUDE.md`, plus agents (`.claude/agents/`), commands (`.claude/commands/`), a hook (`.claude/hooks/pretooluse_bash.py`), and drift tooling (`.claude/harness/check_drift.py`). When you change paths, commands, versions, or architecture, update the harness to match. Structural drift is enforced by `.claude/harness/check_drift.py` (pre-commit and the harness CI workflow); semantic drift is checked by the `/harness-audit` command, which also gates pull-request creation.
