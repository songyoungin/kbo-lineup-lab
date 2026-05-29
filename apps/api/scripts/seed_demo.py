"""Seed the database with fixture demo data and run evaluation + postgame jobs.

This is a DEMO/local helper, not a production ingestion path. It populates an
empty (migrated) database so the API and web app render end-to-end against the
deterministic ``lg_2026_sample.json`` fixture:

1. Load the fixture (teams, players, game, raw snapshots) — idempotent.
2. Ensure a ModelVersion row exists (the fixture does not create one).
3. Trigger a lineup evaluation run for the fixture game — idempotent.
4. Trigger a postgame review run for that evaluation — idempotent.

The evaluation cutoff is derived from the lineup snapshot's ``announced_at`` so
the cutoff-safe snapshot selector always finds the lineup (no hard-coded time).

Run from ``apps/api`` with the same KBO_DATABASE_URL the server uses::

    KBO_DATABASE_URL="sqlite:///./kbo_lineup_lab.db" uv run python scripts/seed_demo.py

Re-running is safe: every step is idempotent.
"""

from __future__ import annotations

from datetime import UTC
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.session import SessionLocal
from app.models.evaluation import ModelVersion
from app.models.snapshot import ActualLineupSnapshot, BoxScoreSnapshot
from app.schemas.postgame import GeneratePostgameReviewRequest
from app.schemas.pregame import ReplayEvaluationRequest
from app.services.fixture_loader import load_fixture_file
from app.services.postgame_reviews import generate_postgame_review_for_request
from app.services.pregame_views import replay_evaluation

FIXTURE_PATH = Path(__file__).resolve().parent.parent / "fixtures" / "lg_2026_sample.json"


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
    """Seed fixture data and produce evaluation + postgame review runs."""
    session = SessionLocal()
    try:
        load_stats = load_fixture_file(FIXTURE_PATH, session)
        print(f"fixture loaded: inserted={load_stats.inserted} skipped={load_stats.skipped}")

        model_version_id = _ensure_model_version(session)

        lineup = session.scalars(select(ActualLineupSnapshot)).first()
        box_score = session.scalars(select(BoxScoreSnapshot)).first()
        if lineup is None or box_score is None:
            raise RuntimeError("fixture did not produce a lineup/box-score snapshot")

        # announced_at is stored naive-UTC; make it tz-aware so the cutoff is valid
        # and at-or-after the lineup announcement (selector uses <= cutoff).
        cutoff = lineup.announced_at.replace(tzinfo=UTC)

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
            f"evaluation run: id={eval_resp.evaluation_run_id} "
            f"created={eval_resp.created} status={eval_resp.status}"
        )

        postgame_resp = generate_postgame_review_for_request(
            session,
            request=GeneratePostgameReviewRequest(
                evaluation_run_id=eval_resp.evaluation_run_id,
                box_score_snapshot_id=int(box_score.id),
            ),
        )
        print(
            f"postgame review run: id={postgame_resp.postgame_review_run_id} "
            f"created={postgame_resp.created} status={postgame_resp.status}"
        )

        session.commit()
        print(f"done. demo game_id={int(lineup.game_id)}")
    finally:
        session.close()


if __name__ == "__main__":
    main()
