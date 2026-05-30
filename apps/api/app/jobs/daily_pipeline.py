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
from app.ingestion.collectors.season_stats import collect_player_season_stats
from app.ingestion.http_client import HttpClient
from app.ingestion.normalizers.box_score import normalize_box_score
from app.ingestion.normalizers.lineup import normalize_lineup
from app.ingestion.normalizers.player_stats import normalize_player_stats
from app.ingestion.normalizers.schedule import normalize_schedule
from app.jobs._run_tracking import get_or_create_ingestion_run
from app.lineup_model.types import Position
from app.models.game import Game
from app.models.player import Player
from app.models.snapshot import ActualLineupSnapshot, IngestionRun

logger = logging.getLogger(__name__)

SOURCE_PREFIX: str = "pipeline:ingest-daily"

# Politeness: per-host throttle for the production client. Tests inject their own
# zero-interval mock client, so this only affects real network runs.
PRODUCTION_MIN_INTERVAL: float = 5.0


@dataclass(frozen=True)
class DailyPipelineResult:
    """Result of a daily pipeline run.

    The ``*_created`` counts report how many snapshots had at least one NEW row
    inserted by their normalizer on THIS run (gated on ``rows_created > 0``).
    They are 0 on the completed-run short-circuit and on a crash-retry where the
    normalizers' content-hash / natural-key dedup guards fire. A 0 therefore does
    NOT mean the snapshot is absent from the DB: it may have been created on an
    earlier run. ``games_found`` is likewise 0 on the completed-run short-circuit
    path (no schedule re-query happens there).

    Attributes:
        ingestion_run_id: PK of the IngestionRun.
        status: "completed" or "failed".
        schedule_created: True when a new schedule payload was stored.
        games_found: Number of LG games found for the target date; 0 on the
            completed-run short-circuit path.
        lineups_created: Number of games whose lineup normalizer inserted at least
            one new row on this run (``rows_created > 0``).
        stat_snapshots_created: Number of games whose player-stats normalizer
            inserted at least one new row on this run (``rows_created > 0``).
        box_scores_created: Number of games whose box-score normalizer inserted at
            least one new row on this run (``rows_created > 0``, not skipped).
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


def _collect_roster_player_season_stats(
    session: Session,
    *,
    ingestion_run: IngestionRun,
    team_id: int,
    http: HttpClient,
) -> int:
    """Fetch season stats for every hitter on the team's roster.

    Drives off ``Player`` rows (excluding pitchers, ``position == 'P'``) rather
    than the announced lineup, so the recommender's candidate pool includes
    bench hitters — not just the starting nine. Each hitter's record endpoint is
    hit once; the production client's per-host throttle keeps the GETs polite.

    Args:
        session: Active SQLAlchemy session. Caller controls the transaction.
        ingestion_run: Parent ingestion run the fetched payloads belong to.
        team_id: Team whose hitters to fetch (LG in the single-team MVP).
        http: Configured HttpClient. Inject a mock client in tests.

    Returns:
        Number of hitters whose season stats were fetched.
    """
    codes = session.execute(
        select(Player.external_id).where(
            Player.team_id == team_id,
            Player.position != Position.P.value,
        )
    ).scalars()
    count = 0
    for code in codes:
        collect_player_season_stats(
            session=session, ingestion_run=ingestion_run, player_code=code, http=http
        )
        count += 1
    return count


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
                # Fetch the preview ONCE per game and run the lineup normalizer
                # on that single raw payload.
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
                    # Now that the lineup is known, fetch each batter's season
                    # record from the per-player Naver endpoint, then normalize
                    # all PLAYER_STATS payloads of this run into one StatSnapshot
                    # so the recommender runs on real season stats.
                    lineup_snapshot = session.get(ActualLineupSnapshot, lr.snapshot_id)
                    if lineup_snapshot is None:
                        raise RuntimeError(
                            f"ActualLineupSnapshot {lr.snapshot_id} not found after normalize"
                        )
                    _collect_roster_player_season_stats(
                        session,
                        ingestion_run=run,
                        team_id=lineup_snapshot.team_id,
                        http=http_client,
                    )
                    ps = normalize_player_stats(
                        session,
                        game_external_id=game.external_id,
                        ingestion_run_id=run.id,
                    )
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
