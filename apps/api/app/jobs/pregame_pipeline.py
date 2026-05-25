"""LG Twins 프리게임 파이프라인: 라인업 수집 → 정규화 → 프리게임 평가."""

from __future__ import annotations

import logging
from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.session import SessionLocal
from app.ingestion.collectors.lineup import LineupStatus, collect_lg_lineup
from app.ingestion.http_client import HttpClient
from app.ingestion.normalizers.lineup import normalize_lineup
from app.jobs._run_tracking import get_or_create_ingestion_run
from app.models.evaluation import ModelVersion
from app.models.snapshot import ActualLineupSnapshot
from app.services.evaluation_runs import get_or_create_evaluation_run
from app.services.lineup_evaluator import evaluate_lineup_for_run
from app.services.snapshot_selector import SnapshotNotFoundError, select_stat_snapshot

logger = logging.getLogger(__name__)

SOURCE_PREFIX: str = "pipeline:ingest-pregame"
MVP_MODEL_NAME: str = "scoring-v1"
MVP_MODEL_VERSION: str = "1.0.0"
MVP_MODEL_ID: str = "rule-based"


@dataclass(frozen=True)
class PregamePipelineResult:
    """프리게임 파이프라인 실행 결과.

    Attributes:
        ingestion_run_id: IngestionRun의 PK.
        status: "completed" 또는 "failed".
        lineup_status: "waiting", "collected", 또는 "skipped_existing".
        evaluation_run_id: LineupEvaluationRun의 PK. 평가가 수행된 경우에만 설정.
        error_message: 실패 시 예외 메시지.
    """

    ingestion_run_id: int
    status: str
    lineup_status: str
    evaluation_run_id: int | None = None
    error_message: str | None = None

    def summary(self) -> str:
        """실행 결과 요약 문자열을 반환한다."""
        eval_part = (
            f"eval_run={self.evaluation_run_id}"
            if self.evaluation_run_id is not None
            else "no_eval"
        )
        return (
            f"pregame pipeline run {self.ingestion_run_id}: {self.status}; "
            f"lineup={self.lineup_status}, {eval_part}"
        )


def run_pregame_pipeline(
    *,
    game_id: str,
    session_factory: Callable[[], AbstractContextManager[Session]] = SessionLocal,
    http: HttpClient | None = None,
) -> PregamePipelineResult:
    """라인업 수집, 정규화, 프리게임 평가를 실행하는 멱등 파이프라인.

    라인업이 아직 발표되지 않은 경우 status='failed', error_message='lineup not announced'를
    반환한다. 스케줄러가 재시도할 수 있다.

    크래시 재시도 시 ``started_at``은 최초 실행 시점을 유지한다 (감사 로그 보존).

    Args:
        game_id: KBO 외부 경기 ID (예: "20260415LGDOO").
        session_factory: SQLAlchemy 세션 팩토리 (테스트에서 대체 가능).
        http: HttpClient 인스턴스. None이면 새로 생성하며 완료 후 닫는다.

    Returns:
        PregamePipelineResult.
    """
    http_client = http or HttpClient()
    own_http = http is None
    with session_factory() as session:
        source = f"{SOURCE_PREFIX}:{game_id}"
        run = get_or_create_ingestion_run(session, source=source)
        if run.status == "completed":
            session.commit()
            return PregamePipelineResult(
                ingestion_run_id=run.id,
                status="completed",
                lineup_status="skipped_existing",
                evaluation_run_id=None,
            )

        # 최초 실행 시점만 기록; 크래시 재시도 시 기존 started_at을 덮어쓰지 않음
        if run.started_at is None:
            run.started_at = datetime.now(UTC)
        run.status = "running"
        session.flush()
        try:
            lineup_result = collect_lg_lineup(
                session=session,
                ingestion_run=run,
                game_id=game_id,
                http=http_client,
            )

            if lineup_result.status == LineupStatus.WAITING:
                run.status = "failed"
                run.finished_at = datetime.now(UTC)
                run.error_message = "lineup not announced"
                session.commit()
                return PregamePipelineResult(
                    ingestion_run_id=run.id,
                    status="failed",
                    lineup_status="waiting",
                    error_message="lineup not announced",
                )

            assert lineup_result.raw_payload is not None
            normalize_result = normalize_lineup(session, lineup_result.raw_payload)

            # 정규화된 라인업 스냅샷에서 game_id, team_id 조회
            lineup_snapshot = session.get(ActualLineupSnapshot, normalize_result.snapshot_id)
            if lineup_snapshot is None:
                raise RuntimeError(
                    f"ActualLineupSnapshot {normalize_result.snapshot_id} not found after normalize"
                )

            game_db_id = lineup_snapshot.game_id
            team_id = lineup_snapshot.team_id

            # 스탯 스냅샷 선택 (수집 시점 기준)
            cutoff_at = lineup_result.fetched_at
            stat_snapshot = select_stat_snapshot(session, team_id=team_id, cutoff_at=cutoff_at)

            # ModelVersion get-or-create (MVP 룰 기반 모델)
            model_version = _get_or_create_model_version(session)

            eval_run, _ = get_or_create_evaluation_run(
                session,
                game_id=game_db_id,
                team_id=team_id,
                evaluation_cutoff_at=cutoff_at,
                stat_snapshot_id=stat_snapshot.id,
                lineup_snapshot_id=normalize_result.snapshot_id,
                model_version_id=model_version.id,
            )
            evaluate_lineup_for_run(session, run=eval_run)

            run.status = "completed"
            run.finished_at = datetime.now(UTC)
            session.commit()
            return PregamePipelineResult(
                ingestion_run_id=run.id,
                status="completed",
                lineup_status="collected",
                evaluation_run_id=eval_run.id,
            )

        except SnapshotNotFoundError as exc:
            run.status = "failed"
            run.finished_at = datetime.now(UTC)
            run.error_message = f"SnapshotNotFoundError: {exc}"
            session.commit()
            logger.warning("pregame pipeline: no stat snapshot for game_id=%s: %s", game_id, exc)
            return PregamePipelineResult(
                ingestion_run_id=run.id,
                status="failed",
                lineup_status="collected",
                error_message=run.error_message,
            )
        except Exception as exc:
            run.status = "failed"
            run.finished_at = datetime.now(UTC)
            run.error_message = f"{type(exc).__name__}: {exc}"
            session.commit()
            logger.exception("pregame pipeline failed for game_id=%s", game_id)
            return PregamePipelineResult(
                ingestion_run_id=run.id,
                status="failed",
                lineup_status="unknown",
                error_message=run.error_message,
            )
        finally:
            if own_http:
                http_client.close()


def _get_or_create_model_version(session: Session) -> ModelVersion:
    """MVP 룰 기반 모델의 ModelVersion을 조회하거나 생성한다.

    Args:
        session: 활성 SQLAlchemy 세션.

    Returns:
        ModelVersion.
    """
    existing = session.execute(
        select(ModelVersion).where(
            ModelVersion.name == MVP_MODEL_NAME,
            ModelVersion.version == MVP_MODEL_VERSION,
        )
    ).scalar_one_or_none()
    if existing is not None:
        return existing
    mv = ModelVersion(
        name=MVP_MODEL_NAME,
        version=MVP_MODEL_VERSION,
        model_id=MVP_MODEL_ID,
        description="MVP rule-based scoring model (Plan 05).",
    )
    session.add(mv)
    session.flush()
    return mv
