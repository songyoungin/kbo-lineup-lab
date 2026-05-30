# KBO Lineup Lab

LG Twins lineup analysis dashboard for pregame lineup evaluation and postgame review.

## Concept

KBO Lineup Lab compares the LG Twins' actual lineup against a data-recommended lineup before the game, then grades the decision after the game using box score results.

The first MVP focuses on one team, deterministic historical simulation, and explainable metrics rather than real-time gamecast data.

The product requires real KBO data, starting with LG Twins schedules, rosters, lineups, player stats, and box scores. Generic baseball or MLB data is not a substitute for product validation.

## Repository

Expected GitHub remote:

```text
git@github.com:songyoungin/kbo-lineup-lab.git
```

## Project Conventions

- Commit messages: English Conventional Commits.
- Documentation: English.
- Code comments: English, and only when they clarify non-obvious behavior.
- User-facing Korean copy may be added separately when the product UI is designed.
- Python tooling: managed with [uv](https://docs.astral.sh/uv/). Do not use pip, poetry, or pip-tools. The repository root is a uv workspace; run `uv sync` from the root and `uv run <cmd>` for all Python commands.

## Development

### Backend

```bash
# Install dependencies (run from repo root)
uv sync

# Start dev server
cd apps/api && uv run uvicorn app.main:app --reload

# Run tests
cd apps/api && uv run pytest
```

### Real data ingestion

Bootstrap a database once (idempotent: applies migrations and seeds the 10 KBO
teams + a default model version), then ingest per day. All commands read
`KBO_DATABASE_URL`; use the same value everywhere.

```bash
cd apps/api
export KBO_DATABASE_URL="sqlite:///./kbo_lineup_lab_real.db"

# One-time setup (safe to re-run)
uv run kbo-lab bootstrap

# Daily: collect schedule + each LG game's lineup, stats, and box score (live Naver)
uv run kbo-lab ingest-daily --date 2026-05-30

# Per game (game id is the Naver game id, e.g. 20260530HTLG02026)
uv run kbo-lab ingest-pregame  --game-id <game-id>   # lineup + pregame evaluation
uv run kbo-lab ingest-postgame --game-id <game-id>   # box score + postgame review
```

For a one-shot run of a single date (bootstrap + ingest + evaluation + postgame
review in one command), use `kbo-lab run`:

```bash
KBO_DATABASE_URL="sqlite:///./kbo_lineup_lab_real.db" uv run kbo-lab run --date 2026-05-30
```

> Scheduling is external for now (e.g. a cron job invoking `kbo-lab ingest-daily`);
> there is no built-in scheduler daemon yet.

### Frontend

```bash
# Install dependencies
cd apps/web && npm install

# Start dev server
cd apps/web && npm run dev

# Lint
cd apps/web && npm run lint

# Format check
cd apps/web && npm run format:check
```

## Pre-commit

Install hooks once after cloning:

```bash
uv sync
uv run pre-commit install
```

Run all hooks on demand (mirrors what CI runs):

```bash
uv run pre-commit run --all-files
```

The same hook set runs automatically in CI via `.github/workflows/pre-commit.yaml` on every pull request targeting `main`.

## Docs

- [MVP Design](docs/superpowers/specs/2026-05-24-lg-twins-lineup-lab-design.md)
- [KBO Source Matrix](docs/data-sources/kbo-source-matrix.md)
