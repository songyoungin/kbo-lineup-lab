"""Ingest REAL LG Twins data for one date and run evaluation + postgame jobs.

Local helper (not a production entrypoint). Unlike ``seed_demo.py`` (which loads
the deterministic fixture), this bootstraps the DB, then runs the real Naver
ingestion pipeline live for the target date and produces the evaluation + postgame
review runs so the API/web render against real data.

Run from ``apps/api`` with the same KBO_DATABASE_URL the server uses::

    KBO_DATABASE_URL="sqlite:///./kbo_lineup_lab_real.db" \
        uv run python scripts/seed_real.py 2026-05-30

Requires live network access to api-gw.sports.naver.com.
"""

from __future__ import annotations

import os
import sys
from datetime import UTC, date

from sqlalchemy import select

from app.db.session import SessionLocal
from app.jobs.bootstrap import run_bootstrap
from app.jobs.daily_pipeline import run_daily_pipeline
from app.models.snapshot import ActualLineupSnapshot, BoxScoreSnapshot
from app.schemas.postgame import GeneratePostgameReviewRequest
from app.schemas.pregame import ReplayEvaluationRequest
from app.services.postgame_reviews import generate_postgame_review_for_request
from app.services.pregame_views import replay_evaluation

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
    """Bootstrap, ingest real data live for the target date, then eval + postgame."""
    target_date = _resolve_target_date()

    # 1. Schema + reference data (idempotent).
    boot = run_bootstrap()
    model_version_id = boot.model_version_id
    print(f"bootstrap: teams created={boot.teams_created}; model_version_id={model_version_id}")

    # 2. Real ingestion pipeline (opens its own session; live Naver fetch).
    result = run_daily_pipeline(target_date=target_date)
    print("pipeline:", result.summary())
    if result.status != "completed":
        print(f"pipeline did not complete: {result.error_message}")
        return

    # 3. Evaluation + postgame review on the ingested snapshots.
    session = SessionLocal()
    try:
        lineup = session.scalars(select(ActualLineupSnapshot)).first()
        box_score = session.scalars(select(BoxScoreSnapshot)).first()
        if lineup is None:
            print("no lineup snapshot ingested; skipping eval/postgame")
            return

        cutoff = lineup.announced_at.replace(tzinfo=UTC)
        try:
            eval_resp = replay_evaluation(
                session,
                request=ReplayEvaluationRequest(
                    game_id=int(lineup.game_id),
                    team_id=int(lineup.team_id),
                    evaluation_cutoff_at=cutoff,
                    model_version_id=model_version_id,
                ),
            )
            print(
                f"evaluation: id={eval_resp.evaluation_run_id} "
                f"created={eval_resp.created} status={eval_resp.status}"
            )
        except Exception as exc:  # noqa: BLE001 - local helper, report and continue
            print(f"evaluation failed: {type(exc).__name__}: {exc}")
            session.rollback()
            return

        if box_score is not None:
            try:
                postgame_resp = generate_postgame_review_for_request(
                    session,
                    request=GeneratePostgameReviewRequest(
                        evaluation_run_id=eval_resp.evaluation_run_id,
                        box_score_snapshot_id=int(box_score.id),
                    ),
                )
                print(
                    f"postgame: id={postgame_resp.postgame_review_run_id} "
                    f"created={postgame_resp.created} status={postgame_resp.status}"
                )
            except Exception as exc:  # noqa: BLE001 - local helper, report and continue
                print(f"postgame failed: {type(exc).__name__}: {exc}")

        session.commit()
        print(f"done. demo game_id={int(lineup.game_id)}")
    finally:
        session.close()


if __name__ == "__main__":
    main()
