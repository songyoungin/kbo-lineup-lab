"""LG Twins 일별 수집 파이프라인: 스케줄 + 로스터 + 선수 스탯."""

from __future__ import annotations

import logging
from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import UTC, date, datetime

from sqlalchemy.orm import Session

from app.db.session import SessionLocal
from app.ingestion.collectors.player_stats import (
    collect_lg_hitter_recent_stats,
    collect_lg_hitter_season_stats,
)
from app.ingestion.collectors.roster import collect_lg_roster
from app.ingestion.collectors.schedule import collect_lg_schedule
from app.ingestion.http_client import HttpClient
from app.jobs._run_tracking import get_or_create_ingestion_run

logger = logging.getLogger(__name__)

SOURCE_PREFIX: str = "pipeline:ingest-daily"


@dataclass(frozen=True)
class DailyPipelineResult:
    """일별 파이프라인 실행 결과.

    Attributes:
        ingestion_run_id: IngestionRun의 PK.
        status: "completed" 또는 "failed".
        schedule_created: 스케줄 페이로드가 새로 생성된 경우 True.
        roster_created: 로스터 페이로드가 새로 생성된 경우 True.
        season_stats_created: 시즌 스탯 페이로드가 새로 생성된 경우 True.
        recent_stats_payloads_created: 새로 생성된 최근 스탯 페이로드 수.
        error_message: 실패 시 예외 메시지.
    """

    ingestion_run_id: int
    status: str
    schedule_created: bool
    roster_created: bool
    season_stats_created: bool
    recent_stats_payloads_created: int
    error_message: str | None = None

    def summary(self) -> str:
        """실행 결과 요약 문자열을 반환한다."""
        return (
            f"daily pipeline run {self.ingestion_run_id}: {self.status}; "
            f"schedule={'+' if self.schedule_created else 'existing'}, "
            f"roster={'+' if self.roster_created else 'existing'}, "
            f"season_stats={'+' if self.season_stats_created else 'existing'}, "
            f"recent_stats_payloads={self.recent_stats_payloads_created}"
        )


def run_daily_pipeline(
    *,
    target_date: date,
    session_factory: Callable[[], AbstractContextManager[Session]] = SessionLocal,
    http: HttpClient | None = None,
) -> DailyPipelineResult:
    """스케줄·로스터·선수 스탯(시즌·최근)을 수집하는 멱등 일별 파이프라인.

    동일 날짜로 재실행해도 이미 완료된 IngestionRun을 그대로 반환하며
    중복 수집을 수행하지 않는다.

    크래시 재시도 시 ``started_at``은 최초 실행 시점을 유지한다 (감사 로그 보존).

    Args:
        target_date: 수집 대상 날짜.
        session_factory: SQLAlchemy 세션 팩토리 (테스트에서 대체 가능).
        http: HttpClient 인스턴스. None이면 새로 생성하며 완료 후 닫는다.

    Returns:
        DailyPipelineResult.
    """
    http_client = http or HttpClient()
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
                roster_created=False,
                season_stats_created=False,
                recent_stats_payloads_created=0,
            )

        # 최초 실행 시점만 기록; 크래시 재시도 시 기존 started_at을 덮어쓰지 않음
        if run.started_at is None:
            run.started_at = datetime.now(UTC)
        run.status = "running"
        session.flush()
        try:
            _, schedule_created = collect_lg_schedule(
                session=session,
                ingestion_run=run,
                date_from=target_date,
                date_to=target_date,
                http=http_client,
            )
            _, roster_created = collect_lg_roster(
                session=session,
                ingestion_run=run,
                season=target_date.year,
                http=http_client,
            )
            _, season_stats_created = collect_lg_hitter_season_stats(
                session=session,
                ingestion_run=run,
                season=target_date.year,
                http=http_client,
            )
            recent_results = collect_lg_hitter_recent_stats(
                session=session,
                ingestion_run=run,
                as_of_date=target_date,
                http=http_client,
            )
            run.status = "completed"
            run.finished_at = datetime.now(UTC)
            session.commit()
            return DailyPipelineResult(
                ingestion_run_id=run.id,
                status="completed",
                schedule_created=schedule_created,
                roster_created=roster_created,
                season_stats_created=season_stats_created,
                recent_stats_payloads_created=sum(1 for _, created in recent_results if created),
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
                roster_created=False,
                season_stats_created=False,
                recent_stats_payloads_created=0,
                error_message=run.error_message,
            )
        finally:
            if own_http:
                http_client.close()
