"""One-shot real-data pipeline: bootstrap + ingest + evaluate + postgame review.

`run_full_pipeline` chains the whole flow for a single date's LG game and returns
a structured result. It is the implementation behind the `kbo-lab run` command and
the `scripts/seed_real.py` demo helper. Live network access (api-gw.sports.naver.com)
is required for the daily ingestion step.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date

from fastapi import HTTPException
from sqlalchemy import select

from app.db.session import SessionLocal
from app.jobs.bootstrap import run_bootstrap
from app.jobs.daily_pipeline import run_daily_pipeline
from app.models.game import Game
from app.models.snapshot import ActualLineupSnapshot, BoxScoreSnapshot
from app.schemas.postgame import GeneratePostgameReviewRequest
from app.schemas.pregame import ReplayEvaluationRequest
from app.services.postgame_reviews import generate_postgame_review_for_request
from app.services.pregame_views import replay_evaluation


@dataclass(frozen=True)
class FullPipelineResult:
    """Outcome of a full one-shot run for a single date.

    Attributes:
        target_date: The date that was ingested.
        daily_status: Status of the daily ingestion run ("completed"/"failed").
        teams_created: Teams seeded by the bootstrap step (0 if all existed).
        game_id: Ingested LG game id, or None if no game/lineup was found.
        evaluation_run_id: Pregame evaluation run id, or None if it did not run.
        postgame_review_run_id: Postgame review run id, or None if it did not run.
        error: Failure message when the analysis phase (eval/review) failed, else None.
    """

    target_date: date
    daily_status: str
    teams_created: int
    game_id: int | None
    evaluation_run_id: int | None
    postgame_review_run_id: int | None
    error: str | None = None

    @property
    def succeeded(self) -> bool:
        """True when ingestion completed and a game was ingested."""
        return (
            self.daily_status == "completed"
            and self.game_id is not None
            and self.evaluation_run_id is not None
            and self.error is None
        )

    def summary(self) -> str:
        """One-line human-readable summary of the run."""
        text = (
            f"run {self.target_date.isoformat()}: daily={self.daily_status}, "
            f"game_id={self.game_id}, eval_run={self.evaluation_run_id}, "
            f"postgame_run={self.postgame_review_run_id}"
        )
        if self.error is not None:
            text += f", error={self.error}"
        return text


def run_full_pipeline(target_date: date) -> FullPipelineResult:
    """Bootstrap, ingest live data for target_date, then evaluate + review.

    Args:
        target_date: The date whose LG game should be ingested and analysed.

    Returns:
        FullPipelineResult capturing the daily status and the ids produced. When
        the daily run fails or no lineup is ingested, the evaluation/review ids
        are None and `succeeded` is False.
    """
    boot = run_bootstrap()

    daily = run_daily_pipeline(target_date=target_date)
    if daily.status != "completed":
        return FullPipelineResult(
            target_date=target_date,
            daily_status=daily.status,
            teams_created=boot.teams_created,
            game_id=None,
            evaluation_run_id=None,
            postgame_review_run_id=None,
        )

    evaluation_run_id: int | None = None
    postgame_review_run_id: int | None = None
    game_id: int | None = None

    with SessionLocal() as session:
        lineup = session.scalars(
            select(ActualLineupSnapshot)
            .join(Game, ActualLineupSnapshot.game_id == Game.id)
            .where(Game.game_date == target_date)
            .order_by(ActualLineupSnapshot.announced_at.desc())
        ).first()
        if lineup is None:
            return FullPipelineResult(
                target_date=target_date,
                daily_status=daily.status,
                teams_created=boot.teams_created,
                game_id=None,
                evaluation_run_id=None,
                postgame_review_run_id=None,
            )

        game_id = int(lineup.game_id)
        box_score = session.scalars(
            select(BoxScoreSnapshot)
            .where(BoxScoreSnapshot.game_id == game_id)
            .order_by(BoxScoreSnapshot.created_at.desc())
        ).first()
        cutoff = lineup.announced_at.replace(tzinfo=UTC)
        try:
            eval_resp = replay_evaluation(
                session,
                request=ReplayEvaluationRequest(
                    game_id=game_id,
                    team_id=int(lineup.team_id),
                    evaluation_cutoff_at=cutoff,
                    model_version_id=boot.model_version_id,
                ),
            )
            evaluation_run_id = eval_resp.evaluation_run_id

            if box_score is not None:
                postgame_resp = generate_postgame_review_for_request(
                    session,
                    request=GeneratePostgameReviewRequest(
                        evaluation_run_id=evaluation_run_id,
                        box_score_snapshot_id=int(box_score.id),
                    ),
                )
                postgame_review_run_id = postgame_resp.postgame_review_run_id

            # replay_evaluation and generate_postgame_review_for_request each commit
            # their own transaction; this commit persists any remaining unflushed state.
            session.commit()
        except HTTPException as exc:
            session.rollback()
            return FullPipelineResult(
                target_date=target_date,
                daily_status=daily.status,
                teams_created=boot.teams_created,
                game_id=game_id,
                evaluation_run_id=evaluation_run_id,
                postgame_review_run_id=postgame_review_run_id,
                error=f"{exc.status_code}: {exc.detail}",
            )

    return FullPipelineResult(
        target_date=target_date,
        daily_status=daily.status,
        teams_created=boot.teams_created,
        game_id=game_id,
        evaluation_run_id=evaluation_run_id,
        postgame_review_run_id=postgame_review_run_id,
    )
