"""Ingest REAL LG Twins data for one date and run evaluation + postgame jobs.

Local helper (not a production entrypoint). Thin wrapper over the `kbo-lab run`
command's orchestration (`app.jobs.full_pipeline.run_full_pipeline`): it bootstraps,
runs the live Naver ingestion pipeline for the target date, and produces the
evaluation + postgame review runs so the API/web render against real data.

Run from ``apps/api`` with the same KBO_DATABASE_URL the server uses::

    KBO_DATABASE_URL="sqlite:///./kbo_lineup_lab_real.db" \
        uv run python scripts/seed_real.py 2026-05-30

Prefer ``uv run kbo-lab run --date 2026-05-30``; this script remains for convenience.
Requires live network access to api-gw.sports.naver.com.
"""

from __future__ import annotations

import os
import sys
from datetime import date

from app.jobs.full_pipeline import run_full_pipeline

_DEFAULT_TARGET_DATE = date(2025, 5, 14)  # verified game: Kiwoom (WO) @ LG, final


def _resolve_target_date() -> date:
    """Date to ingest: first CLI arg, else SEED_REAL_DATE env, else the default."""
    if len(sys.argv) > 1:
        return date.fromisoformat(sys.argv[1])
    env_value = os.environ.get("SEED_REAL_DATE")
    if env_value:
        return date.fromisoformat(env_value)
    return _DEFAULT_TARGET_DATE


def main() -> None:
    """Resolve the date and run the full real-data pipeline, printing the summary."""
    result = run_full_pipeline(_resolve_target_date())
    print(result.summary())


if __name__ == "__main__":
    main()
