"""Ingest REAL LG Twins data for one date and run evaluation + postgame jobs.

Local helper (not a production entrypoint). Unlike ``seed_demo.py`` (which loads
the deterministic fixture), this seeds the KBO teams + a ModelVersion, then runs
the real Naver ingestion pipeline live for ``TARGET_DATE`` and produces the
evaluation + postgame review runs so the API/web render against real data.

Run from ``apps/api`` with the same KBO_DATABASE_URL the server uses::

    KBO_DATABASE_URL="sqlite:///./kbo_lineup_lab_real.db" uv run python scripts/seed_real.py

Requires live network access to api-gw.sports.naver.com.
"""

from __future__ import annotations

from datetime import UTC, date

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.session import SessionLocal
from app.ingestion.game_id import TEAM_CODES
from app.jobs.daily_pipeline import run_daily_pipeline
from app.models.evaluation import ModelVersion
from app.models.snapshot import ActualLineupSnapshot, BoxScoreSnapshot
from app.models.team import Team
from app.schemas.postgame import GeneratePostgameReviewRequest
from app.schemas.pregame import ReplayEvaluationRequest
from app.services.postgame_reviews import generate_postgame_review_for_request
from app.services.pregame_views import replay_evaluation

TARGET_DATE = date(2025, 5, 14)  # verified game: Kiwoom (WO) @ LG, final


def _seed_teams(session: Session) -> int:
    """Insert the 10 KBO teams (idempotent). Returns the number created."""
    created = 0
    for code, name in TEAM_CODES.items():
        existing = session.scalars(select(Team).where(Team.code == code)).first()
        if existing is None:
            session.add(Team(code=code, name=name))
            created += 1
    session.flush()
    return created


def _ensure_model_version(session: Session) -> int:
    """Return an existing ModelVersion id, creating a default one if none exist."""
    existing = session.scalars(select(ModelVersion)).first()
    if existing is not None:
        return int(existing.id)
    model_version = ModelVersion(
        name="heuristic-v1",
        version="v1",
        model_id="internal/lineup-score-v1",
    )
    session.add(model_version)
    session.flush()
    return int(model_version.id)


def main() -> None:
    """Seed teams, ingest real data live, then run eval + postgame review."""
    # 1. Teams + ModelVersion (own transaction).
    session = SessionLocal()
    try:
        created = _seed_teams(session)
        model_version_id = _ensure_model_version(session)
        session.commit()
        print(f"teams: created={created}; model_version_id={model_version_id}")
    finally:
        session.close()

    # 2. Real ingestion pipeline (opens its own session; live Naver fetch).
    result = run_daily_pipeline(target_date=TARGET_DATE)
    print("pipeline:", result.summary())
    if result.status != "completed":
        print(f"pipeline did not complete: {result.error_message}")
        return

    # 3. Evaluation + postgame review on the ingested snapshots.
    session = SessionLocal()
    try:
        model_version_id = _ensure_model_version(session)
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
