"""LG Twins daily ingestion pipeline: schedule, then per-game lineup/stats/box score."""

from __future__ import annotations

import logging
from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import UTC, date, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.session import SessionLocal
from app.ingestion.collectors.box_score import BoxScoreStatus, collect_lg_box_score
from app.ingestion.collectors.lineup import LineupStatus, collect_lg_lineup
from app.ingestion.collectors.schedule import collect_lg_schedule
from app.ingestion.http_client import HttpClient
from app.ingestion.normalizers.box_score import normalize_box_score
from app.ingestion.normalizers.lineup import normalize_lineup
from app.ingestion.normalizers.player_stats import normalize_player_stats
from app.ingestion.normalizers.schedule import normalize_schedule
from app.jobs._run_tracking import get_or_create_ingestion_run
from app.models.game import Game

logger = logging.getLogger(__name__)

SOURCE_PREFIX: str = "pipeline:ingest-daily"

# Politeness: per-host throttle for the production client. Tests inject their own
# zero-interval mock client, so this only affects real network runs.
PRODUCTION_MIN_INTERVAL: float = 5.0


@dataclass(frozen=True)
class DailyPipelineResult:
    """Result of a daily pipeline run.

    Attributes:
        ingestion_run_id: PK of the IngestionRun.
        status: "completed" or "failed".
        schedule_created: True when a new schedule payload was stored.
        games_found: Number of LG games found for the target date.
        lineups_created: Number of games for which a lineup snapshot was created.
        stat_snapshots_created: Number of games for which a stat snapshot was created.
        box_scores_created: Number of games for which a box score snapshot was created.
        error_message: Exception message when the run failed.
    """

    ingestion_run_id: int
    status: str
    schedule_created: bool
    games_found: int
    lineups_created: int
    stat_snapshots_created: int
    box_scores_created: int
    error_message: str | None = None

    def summary(self) -> str:
        """Return a one-line summary of the run result."""
        return (
            f"daily pipeline run {self.ingestion_run_id}: {self.status}; "
            f"schedule={'+' if self.schedule_created else 'existing'}, "
            f"games={self.games_found}, "
            f"lineups={self.lineups_created}, "
            f"stat_snapshots={self.stat_snapshots_created}, "
            f"box_scores={self.box_scores_created}"
        )


def run_daily_pipeline(
    *,
    target_date: date,
    session_factory: Callable[[], AbstractContextManager[Session]] = SessionLocal,
    http: HttpClient | None = None,
) -> DailyPipelineResult:
    """Idempotent daily pipeline driving the Naver collectors end-to-end.

    For ``target_date`` the pipeline collects the LG schedule, then for each LG
    game collects+normalizes the lineup, player stats, and box score.

    Re-running for the same date returns the already-completed IngestionRun
    without re-collecting anything. On crash-retry, ``started_at`` is preserved
    so the audit log keeps the original start time.

    Args:
        target_date: The date to ingest.
        session_factory: SQLAlchemy session factory (overridable in tests).
        http: HttpClient instance. When None a new one is created (with a per-host
            throttle) and closed afterwards.

    Returns:
        DailyPipelineResult.
    """
    http_client = http or HttpClient(min_interval=PRODUCTION_MIN_INTERVAL)
    own_http = http is None
    with session_factory() as session:
        source = f"{SOURCE_PREFIX}:{target_date.isoformat()}"
        run = get_or_create_ingestion_run(session, source=source)
        if run.status == "completed":
            session.commit()
            return DailyPipelineResult(
                ingestion_run_id=run.id,
                status="completed",
                schedule_created=False,
                games_found=0,
                lineups_created=0,
                stat_snapshots_created=0,
                box_scores_created=0,
            )

        # Record the start time only once; do not overwrite it on crash-retry.
        if run.started_at is None:
            run.started_at = datetime.now(UTC)
        run.status = "running"
        session.flush()
        try:
            schedule_payload, schedule_created = collect_lg_schedule(
                session=session,
                ingestion_run=run,
                date_from=target_date,
                date_to=target_date,
                http=http_client,
            )
            normalize_schedule(session, schedule_payload)

            # The schedule normalizer only creates LG games, so every Game row
            # for this date is an LG game.
            games = list(
                session.execute(select(Game).where(Game.game_date == target_date)).scalars()
            )

            lineups_created = 0
            stat_snapshots_created = 0
            box_scores_created = 0
            for game in games:
                # Fetch the preview ONCE per game: the lineup collector and the
                # season-stats collector both hit the same Naver /preview URL.
                # Calling both would issue two identical network GETs (impolite),
                # so we collect the preview once and run BOTH the lineup and the
                # player-stats normalizers on that single raw payload.
                lineup_result = collect_lg_lineup(
                    session=session,
                    ingestion_run=run,
                    game_id=game.external_id,
                    http=http_client,
                )
                if (
                    lineup_result.status == LineupStatus.COLLECTED
                    and lineup_result.raw_payload is not None
                ):
                    lr = normalize_lineup(session, lineup_result.raw_payload)
                    if lr.rows_created > 0:
                        lineups_created += 1
                    ps = normalize_player_stats(session, lineup_result.raw_payload)
                    if ps.rows_created > 0:
                        stat_snapshots_created += 1

                box_result = collect_lg_box_score(
                    session=session,
                    ingestion_run=run,
                    game_id=game.external_id,
                    http=http_client,
                )
                if (
                    box_result.status == BoxScoreStatus.COLLECTED
                    and box_result.raw_payload is not None
                ):
                    br = normalize_box_score(session, box_result.raw_payload)
                    if not br.skipped_not_final and br.rows_created > 0:
                        box_scores_created += 1

            run.status = "completed"
            run.finished_at = datetime.now(UTC)
            session.commit()
            return DailyPipelineResult(
                ingestion_run_id=run.id,
                status="completed",
                schedule_created=schedule_created,
                games_found=len(games),
                lineups_created=lineups_created,
                stat_snapshots_created=stat_snapshots_created,
                box_scores_created=box_scores_created,
            )
        except Exception as exc:
            run.status = "failed"
            run.finished_at = datetime.now(UTC)
            run.error_message = f"{type(exc).__name__}: {exc}"
            session.commit()
            logger.exception("daily pipeline failed for %s", target_date)
            return DailyPipelineResult(
                ingestion_run_id=run.id,
                status="failed",
                schedule_created=False,
                games_found=0,
                lineups_created=0,
                stat_snapshots_created=0,
                box_scores_created=0,
                error_message=run.error_message,
            )
        finally:
            if own_http:
                http_client.close()
