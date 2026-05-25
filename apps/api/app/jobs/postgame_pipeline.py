"""LG Twins 포스트게임 파이프라인: 박스스코어 수집 → 정규화 → 포스트게임 리뷰."""

from __future__ import annotations

import logging
from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.session import SessionLocal
from app.ingestion.collectors.box_score import BoxScoreStatus, collect_lg_box_score
from app.ingestion.http_client import HttpClient
from app.ingestion.normalizers.box_score import normalize_box_score
from app.jobs._run_tracking import get_or_create_ingestion_run
from app.models.evaluation import LineupEvaluationRun
from app.models.snapshot import BoxScoreSnapshot
from app.services.postgame_reviews import generate_review_for_run, get_or_create_postgame_review

logger = logging.getLogger(__name__)

SOURCE_PREFIX: str = "pipeline:ingest-postgame"


@dataclass(frozen=True)
class PostgamePipelineResult:
    """포스트게임 파이프라인 실행 결과.

    Attributes:
        ingestion_run_id: IngestionRun의 PK.
        status: "completed" 또는 "failed".
        box_score_status: "waiting", "collected", 또는 "skipped_existing".
        postgame_review_run_id: PostgameReviewRun의 PK. 리뷰가 생성된 경우에만 설정.
        error_message: 실패 시 예외 메시지.
    """

    ingestion_run_id: int
    status: str
    box_score_status: str
    postgame_review_run_id: int | None = None
    error_message: str | None = None

    def summary(self) -> str:
        """실행 결과 요약 문자열을 반환한다."""
        review_part = (
            f"review_run={self.postgame_review_run_id}"
            if self.postgame_review_run_id is not None
            else "no_review"
        )
        return (
            f"postgame pipeline run {self.ingestion_run_id}: {self.status}; "
            f"box_score={self.box_score_status}, {review_part}"
        )


def run_postgame_pipeline(
    *,
    game_id: str,
    session_factory: Callable[[], AbstractContextManager[Session]] = SessionLocal,
    http: HttpClient | None = None,
) -> PostgamePipelineResult:
    """박스스코어 수집, 정규화, 포스트게임 리뷰를 실행하는 멱등 파이프라인.

    게임이 아직 종료되지 않은 경우 status='failed', error_message='box score not final'을
    반환한다. 스케줄러가 재시도할 수 있다.

    이 파이프라인을 실행하기 전에 pregame 파이프라인이 완료되어 있어야 한다
    (LineupEvaluationRun이 존재해야 함).

    크래시 재시도 시 ``started_at``은 최초 실행 시점을 유지한다 (감사 로그 보존).

    Args:
        game_id: KBO 외부 경기 ID (예: "20260415LGDOO").
        session_factory: SQLAlchemy 세션 팩토리 (테스트에서 대체 가능).
        http: HttpClient 인스턴스. None이면 새로 생성하며 완료 후 닫는다.

    Returns:
        PostgamePipelineResult.
    """
    http_client = http or HttpClient()
    own_http = http is None
    with session_factory() as session:
        source = f"{SOURCE_PREFIX}:{game_id}"
        run = get_or_create_ingestion_run(session, source=source)
        if run.status == "completed":
            session.commit()
            return PostgamePipelineResult(
                ingestion_run_id=run.id,
                status="completed",
                box_score_status="skipped_existing",
                postgame_review_run_id=None,
            )

        # 최초 실행 시점만 기록; 크래시 재시도 시 기존 started_at을 덮어쓰지 않음
        if run.started_at is None:
            run.started_at = datetime.now(UTC)
        run.status = "running"
        session.flush()
        try:
            box_result = collect_lg_box_score(
                session=session,
                ingestion_run=run,
                game_id=game_id,
                http=http_client,
            )

            if box_result.status == BoxScoreStatus.WAITING:
                run.status = "failed"
                run.finished_at = datetime.now(UTC)
                run.error_message = "box score not final"
                session.commit()
                return PostgamePipelineResult(
                    ingestion_run_id=run.id,
                    status="failed",
                    box_score_status="waiting",
                    error_message="box score not final",
                )

            assert box_result.raw_payload is not None
            normalize_result = normalize_box_score(session, box_result.raw_payload)

            if normalize_result.skipped_not_final:
                run.status = "failed"
                run.finished_at = datetime.now(UTC)
                run.error_message = "box score not final after normalization"
                session.commit()
                return PostgamePipelineResult(
                    ingestion_run_id=run.id,
                    status="failed",
                    box_score_status="not_final",
                    error_message="box score not final after normalization",
                )

            if normalize_result.snapshot_id is None:
                raise RuntimeError("normalize_box_score returned None snapshot_id unexpectedly")

            box_snapshot = session.get(BoxScoreSnapshot, normalize_result.snapshot_id)
            if box_snapshot is None:
                raise RuntimeError(
                    f"BoxScoreSnapshot {normalize_result.snapshot_id} not found after normalize"
                )

            # 해당 경기의 최신 완료된 LineupEvaluationRun 조회
            eval_run = _find_latest_completed_eval_run(session, game_db_id=box_snapshot.game_id)
            if eval_run is None:
                run.status = "failed"
                run.finished_at = datetime.now(UTC)
                run.error_message = (
                    f"no completed LineupEvaluationRun for game_id={box_snapshot.game_id}; "
                    "run pregame pipeline first"
                )
                session.commit()
                return PostgamePipelineResult(
                    ingestion_run_id=run.id,
                    status="failed",
                    box_score_status="collected",
                    error_message=run.error_message,
                )

            review_run, _ = get_or_create_postgame_review(
                session,
                evaluation_run_id=eval_run.id,
                box_score_snapshot_id=normalize_result.snapshot_id,
            )
            generate_review_for_run(session, run=review_run)

            run.status = "completed"
            run.finished_at = datetime.now(UTC)
            session.commit()
            return PostgamePipelineResult(
                ingestion_run_id=run.id,
                status="completed",
                box_score_status="collected",
                postgame_review_run_id=review_run.id,
            )

        except Exception as exc:
            run.status = "failed"
            run.finished_at = datetime.now(UTC)
            run.error_message = f"{type(exc).__name__}: {exc}"
            session.commit()
            logger.exception("postgame pipeline failed for game_id=%s", game_id)
            return PostgamePipelineResult(
                ingestion_run_id=run.id,
                status="failed",
                box_score_status="unknown",
                error_message=run.error_message,
            )
        finally:
            if own_http:
                http_client.close()


def _find_latest_completed_eval_run(
    session: Session,
    *,
    game_db_id: int,
) -> LineupEvaluationRun | None:
    """해당 경기에 대해 최신 완료된 LineupEvaluationRun을 반환한다.

    Args:
        session: 활성 SQLAlchemy 세션.
        game_db_id: Game 테이블의 PK.

    Returns:
        최신 완료된 LineupEvaluationRun, 없으면 None.
    """
    return (
        session.execute(
            select(LineupEvaluationRun)
            .where(
                LineupEvaluationRun.game_id == game_db_id,
                LineupEvaluationRun.status == "completed",
            )
            .order_by(LineupEvaluationRun.finished_at.desc())
            .limit(1)
        )
        .scalars()
        .first()
    )
