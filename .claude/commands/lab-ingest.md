---
description: Run the KBO data ingestion commands against the configured database.
---

Run KBO ingestion with the `kbo-lab` CLI from `apps/api` (e.g. `uv run kbo-lab ingest-daily`). Subcommands: ingest-daily, ingest-pregame, ingest-postgame.

Before running, confirm which database is configured (KBO_DATABASE_URL) — for the real Supabase database follow the running-supabase-dev skill; for local SQLite fixtures follow the running-fixture-demo skill. Report what was ingested and any errors.
