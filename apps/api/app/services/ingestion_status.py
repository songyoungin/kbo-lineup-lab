"""수집 파이프라인 상태 집계 서비스.

각 게임별로 스케줄, 로스터, 선수 스탯, 라인업, 스탯 스냅샷, 프리게임 평가,
박스스코어, 포스트게임 리뷰의 수집·정규화·완료 상태를 조회한다.

**MVP 한계:** `needs_review` 상태는 예약되어 있으나 현재 DB에 persist되지 않으므로
이 서비스에서는 채워지지 않는다. Plan 17 정규화기가 반환하는 `needs_review_reasons`는
인메모리에만 존재한다. 후속 작업(Plan 17 follow-up)에서 `IngestionRun.error_message`
혹은 별도 컬럼에 reasons를 저장하면 이 서비스에서 집계할 수 있다.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.evaluation import LineupEvaluationRun
from app.models.game import Game
from app.models.postgame import PostgameReviewRun
from app.models.snapshot import (
    ActualLineupSnapshot,
    BoxScoreSnapshot,
    IngestionRun,
    RawIngestionPayload,
    StatSnapshot,
)

# ---------------------------------------------------------------------------
# Source prefix constants (mirrors jobs/*.py)
# ---------------------------------------------------------------------------
_DAILY_PREFIX = "pipeline:ingest-daily:"
_PREGAME_PREFIX = "pipeline:ingest-pregame:"
_POSTGAME_PREFIX = "pipeline:ingest-postgame:"


# ---------------------------------------------------------------------------
# Domain dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CategoryStatus:
    """단일 수집 카테고리의 현재 상태.

    Attributes:
        category: 카테고리 식별자 (예: "schedule", "lineup").
        status: 파이프라인 상태 어휘 중 하나.
        raw_payload_id: 해당 카테고리의 최신 RawIngestionPayload PK.
        snapshot_id: 해당 카테고리의 최신 스냅샷 PK (정규화된 경우).
        run_id: 관련 IngestionRun 또는 평가/리뷰 run의 PK.
        error_message: 실패 상태인 경우 에러 메시지.
    """

    category: str
    status: str  # waiting | collected | normalized | complete | failed | needs_review
    raw_payload_id: int | None = None
    snapshot_id: int | None = None
    run_id: int | None = None
    error_message: str | None = None


@dataclass(frozen=True)
class GameIngestionStatus:
    """단일 게임의 전체 수집 파이프라인 상태.

    Attributes:
        game_id: Game 테이블 PK.
        game_external_id: KBO 외부 경기 ID (예: "20260415LGDOO").
        game_date: 경기 날짜.
        categories: 카테고리별 상태 튜플.
    """

    game_id: int
    game_external_id: str
    game_date: date
    categories: tuple[CategoryStatus, ...]


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _run_status_to_pipeline(run: IngestionRun) -> str:
    """IngestionRun.status를 파이프라인 어휘로 변환한다.

    Args:
        run: IngestionRun ORM 인스턴스.

    Returns:
        "failed" 또는 "complete" (pending/running은 "waiting"으로 취급).
    """
    if run.status == "failed":
        return "failed"
    if run.status == "completed":
        return "complete"
    return "waiting"


def _payload_for_game_category(
    session: Session,
    *,
    game_external_id: str,
    game_date: date,
    category: str,
) -> RawIngestionPayload | None:
    """게임과 카테고리에 해당하는 최신 RawIngestionPayload를 반환한다.

    daily 카테고리(schedule, roster, player_stats)는 game_external_id 없이
    날짜 기반 ingestion_run에 연결되므로, 날짜 기반 run에서 탐색한다.
    lineup과 box_score는 게임별 run에 연결된다.

    Args:
        session: 활성 SQLAlchemy 세션.
        game_external_id: KBO 외부 경기 ID.
        game_date: 경기 날짜.
        category: 페이로드 카테고리 문자열.

    Returns:
        가장 최근 RawIngestionPayload 또는 None.
    """
    if category in ("schedule", "roster", "player_stats"):
        # 날짜 기반 daily run에 연결된 페이로드 탐색
        daily_source = f"{_DAILY_PREFIX}{game_date.isoformat()}"
        run = session.execute(
            select(IngestionRun).where(IngestionRun.source == daily_source)
        ).scalar_one_or_none()
        if run is None:
            return None
        return session.execute(
            select(RawIngestionPayload)
            .where(
                RawIngestionPayload.ingestion_run_id == run.id,
                RawIngestionPayload.category == category,
            )
            .order_by(RawIngestionPayload.fetched_at.desc())
            .limit(1)
        ).scalar_one_or_none()

    if category == "lineup":
        pregame_source = f"{_PREGAME_PREFIX}{game_external_id}"
        run = session.execute(
            select(IngestionRun).where(IngestionRun.source == pregame_source)
        ).scalar_one_or_none()
        if run is None:
            return None
        return session.execute(
            select(RawIngestionPayload)
            .where(
                RawIngestionPayload.ingestion_run_id == run.id,
                RawIngestionPayload.category == "lineup",
            )
            .order_by(RawIngestionPayload.fetched_at.desc())
            .limit(1)
        ).scalar_one_or_none()

    if category == "box_score":
        postgame_source = f"{_POSTGAME_PREFIX}{game_external_id}"
        run = session.execute(
            select(IngestionRun).where(IngestionRun.source == postgame_source)
        ).scalar_one_or_none()
        if run is None:
            return None
        return session.execute(
            select(RawIngestionPayload)
            .where(
                RawIngestionPayload.ingestion_run_id == run.id,
                RawIngestionPayload.category == "box_score",
            )
            .order_by(RawIngestionPayload.fetched_at.desc())
            .limit(1)
        ).scalar_one_or_none()

    return None


def _build_daily_category_status(
    session: Session,
    *,
    game_date: date,
    category: str,
) -> CategoryStatus:
    """날짜 기반 daily IngestionRun에서 카테고리 상태를 생성한다.

    Args:
        session: 활성 SQLAlchemy 세션.
        game_date: 경기 날짜.
        category: "schedule", "roster", "player_stats" 중 하나.

    Returns:
        CategoryStatus.
    """
    daily_source = f"{_DAILY_PREFIX}{game_date.isoformat()}"
    run = session.execute(
        select(IngestionRun).where(IngestionRun.source == daily_source)
    ).scalar_one_or_none()

    if run is None:
        return CategoryStatus(category=category, status="waiting")

    if run.status == "failed":
        return CategoryStatus(
            category=category,
            status="failed",
            run_id=run.id,
            error_message=run.error_message,
        )

    # run이 존재하면 페이로드 확인
    payload = session.execute(
        select(RawIngestionPayload)
        .where(
            RawIngestionPayload.ingestion_run_id == run.id,
            RawIngestionPayload.category == category,
        )
        .order_by(RawIngestionPayload.fetched_at.desc())
        .limit(1)
    ).scalar_one_or_none()

    if payload is None:
        return CategoryStatus(category=category, status="waiting", run_id=run.id)

    return CategoryStatus(
        category=category,
        status="collected",
        raw_payload_id=payload.id,
        run_id=run.id,
    )


def _build_stat_snapshot_status(
    session: Session,
    *,
    game_date: date,
) -> CategoryStatus:
    """stat_snapshot 카테고리 상태를 생성한다.

    StatSnapshot은 daily run에 연결되며, 존재하면 "normalized" 상태다.

    Args:
        session: 활성 SQLAlchemy 세션.
        game_date: 경기 날짜.

    Returns:
        CategoryStatus.
    """
    daily_source = f"{_DAILY_PREFIX}{game_date.isoformat()}"
    run = session.execute(
        select(IngestionRun).where(IngestionRun.source == daily_source)
    ).scalar_one_or_none()

    if run is None:
        return CategoryStatus(category="stat_snapshot", status="waiting")

    if run.status == "failed":
        return CategoryStatus(
            category="stat_snapshot",
            status="failed",
            run_id=run.id,
            error_message=run.error_message,
        )

    snapshot = session.execute(
        select(StatSnapshot)
        .where(StatSnapshot.ingestion_run_id == run.id)
        .order_by(StatSnapshot.created_at.desc())
        .limit(1)
    ).scalar_one_or_none()

    if snapshot is None:
        return CategoryStatus(category="stat_snapshot", status="collected", run_id=run.id)

    return CategoryStatus(
        category="stat_snapshot",
        status="normalized",
        snapshot_id=snapshot.id,
        run_id=run.id,
    )


def _build_lineup_status(
    session: Session,
    *,
    game_id: int,
    game_external_id: str,
) -> CategoryStatus:
    """lineup 카테고리 상태를 생성한다.

    Args:
        session: 활성 SQLAlchemy 세션.
        game_id: Game 테이블 PK.
        game_external_id: KBO 외부 경기 ID.

    Returns:
        CategoryStatus.
    """
    pregame_source = f"{_PREGAME_PREFIX}{game_external_id}"
    run = session.execute(
        select(IngestionRun).where(IngestionRun.source == pregame_source)
    ).scalar_one_or_none()

    if run is None:
        return CategoryStatus(category="lineup", status="waiting")

    if run.status == "failed":
        return CategoryStatus(
            category="lineup",
            status="failed",
            run_id=run.id,
            error_message=run.error_message,
        )

    payload = session.execute(
        select(RawIngestionPayload)
        .where(
            RawIngestionPayload.ingestion_run_id == run.id,
            RawIngestionPayload.category == "lineup",
        )
        .order_by(RawIngestionPayload.fetched_at.desc())
        .limit(1)
    ).scalar_one_or_none()

    if payload is None:
        return CategoryStatus(category="lineup", status="waiting", run_id=run.id)

    # 스냅샷 확인
    snapshot = session.execute(
        select(ActualLineupSnapshot)
        .where(ActualLineupSnapshot.game_id == game_id)
        .order_by(ActualLineupSnapshot.created_at.desc())
        .limit(1)
    ).scalar_one_or_none()

    if snapshot is None:
        return CategoryStatus(
            category="lineup",
            status="collected",
            raw_payload_id=payload.id,
            run_id=run.id,
        )

    return CategoryStatus(
        category="lineup",
        status="normalized",
        raw_payload_id=payload.id,
        snapshot_id=snapshot.id,
        run_id=run.id,
    )


def _build_evaluation_status(
    session: Session,
    *,
    game_id: int,
    game_external_id: str,
) -> CategoryStatus:
    """evaluation 카테고리 상태를 생성한다.

    Args:
        session: 활성 SQLAlchemy 세션.
        game_id: Game 테이블 PK.
        game_external_id: KBO 외부 경기 ID.

    Returns:
        CategoryStatus.
    """
    pregame_source = f"{_PREGAME_PREFIX}{game_external_id}"
    run = session.execute(
        select(IngestionRun).where(IngestionRun.source == pregame_source)
    ).scalar_one_or_none()

    if run is None:
        return CategoryStatus(category="evaluation", status="waiting")

    eval_run = session.execute(
        select(LineupEvaluationRun)
        .where(LineupEvaluationRun.game_id == game_id)
        .order_by(LineupEvaluationRun.created_at.desc())
        .limit(1)
    ).scalar_one_or_none()

    if eval_run is None:
        if run.status == "failed":
            return CategoryStatus(
                category="evaluation",
                status="failed",
                run_id=run.id,
                error_message=run.error_message,
            )
        return CategoryStatus(category="evaluation", status="waiting", run_id=run.id)

    if eval_run.status == "failed":
        return CategoryStatus(
            category="evaluation",
            status="failed",
            run_id=eval_run.id,
            error_message=eval_run.error_message,
        )

    if eval_run.status == "completed":
        return CategoryStatus(
            category="evaluation",
            status="complete",
            run_id=eval_run.id,
        )

    return CategoryStatus(
        category="evaluation",
        status="waiting",
        run_id=eval_run.id,
    )


def _build_box_score_status(
    session: Session,
    *,
    game_id: int,
    game_external_id: str,
) -> CategoryStatus:
    """box_score 카테고리 상태를 생성한다.

    Args:
        session: 활성 SQLAlchemy 세션.
        game_id: Game 테이블 PK.
        game_external_id: KBO 외부 경기 ID.

    Returns:
        CategoryStatus.
    """
    postgame_source = f"{_POSTGAME_PREFIX}{game_external_id}"
    run = session.execute(
        select(IngestionRun).where(IngestionRun.source == postgame_source)
    ).scalar_one_or_none()

    if run is None:
        return CategoryStatus(category="box_score", status="waiting")

    if run.status == "failed":
        payload = session.execute(
            select(RawIngestionPayload)
            .where(
                RawIngestionPayload.ingestion_run_id == run.id,
                RawIngestionPayload.category == "box_score",
            )
            .order_by(RawIngestionPayload.fetched_at.desc())
            .limit(1)
        ).scalar_one_or_none()
        return CategoryStatus(
            category="box_score",
            status="failed",
            raw_payload_id=payload.id if payload else None,
            run_id=run.id,
            error_message=run.error_message,
        )

    payload = session.execute(
        select(RawIngestionPayload)
        .where(
            RawIngestionPayload.ingestion_run_id == run.id,
            RawIngestionPayload.category == "box_score",
        )
        .order_by(RawIngestionPayload.fetched_at.desc())
        .limit(1)
    ).scalar_one_or_none()

    if payload is None:
        return CategoryStatus(category="box_score", status="waiting", run_id=run.id)

    snapshot = session.execute(
        select(BoxScoreSnapshot)
        .where(BoxScoreSnapshot.game_id == game_id)
        .order_by(BoxScoreSnapshot.created_at.desc())
        .limit(1)
    ).scalar_one_or_none()

    if snapshot is None:
        return CategoryStatus(
            category="box_score",
            status="collected",
            raw_payload_id=payload.id,
            run_id=run.id,
        )

    return CategoryStatus(
        category="box_score",
        status="normalized",
        raw_payload_id=payload.id,
        snapshot_id=snapshot.id,
        run_id=run.id,
    )


def _build_postgame_review_status(
    session: Session,
    *,
    game_id: int,
    game_external_id: str,
) -> CategoryStatus:
    """postgame_review 카테고리 상태를 생성한다.

    Args:
        session: 활성 SQLAlchemy 세션.
        game_id: Game 테이블 PK.
        game_external_id: KBO 외부 경기 ID.

    Returns:
        CategoryStatus.
    """
    postgame_source = f"{_POSTGAME_PREFIX}{game_external_id}"
    run = session.execute(
        select(IngestionRun).where(IngestionRun.source == postgame_source)
    ).scalar_one_or_none()

    if run is None:
        return CategoryStatus(category="postgame_review", status="waiting")

    # PostgameReviewRun을 game_id 기반으로 탐색 (box_score_snapshot→game_id 조인)
    box_snapshot = session.execute(
        select(BoxScoreSnapshot)
        .where(BoxScoreSnapshot.game_id == game_id)
        .order_by(BoxScoreSnapshot.created_at.desc())
        .limit(1)
    ).scalar_one_or_none()

    if box_snapshot is None:
        if run.status == "failed":
            return CategoryStatus(
                category="postgame_review",
                status="failed",
                run_id=run.id,
                error_message=run.error_message,
            )
        return CategoryStatus(category="postgame_review", status="waiting", run_id=run.id)

    review_run = session.execute(
        select(PostgameReviewRun)
        .where(PostgameReviewRun.box_score_snapshot_id == box_snapshot.id)
        .order_by(PostgameReviewRun.created_at.desc())
        .limit(1)
    ).scalar_one_or_none()

    if review_run is None:
        if run.status == "failed":
            return CategoryStatus(
                category="postgame_review",
                status="failed",
                run_id=run.id,
                error_message=run.error_message,
            )
        return CategoryStatus(category="postgame_review", status="waiting", run_id=run.id)

    if review_run.status == "failed":
        return CategoryStatus(
            category="postgame_review",
            status="failed",
            run_id=review_run.id,
            error_message=review_run.error_message,
        )

    if review_run.status == "completed":
        return CategoryStatus(
            category="postgame_review",
            status="complete",
            run_id=review_run.id,
        )

    return CategoryStatus(
        category="postgame_review",
        status="waiting",
        run_id=review_run.id,
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def build_game_ingestion_status(session: Session, game_id: int) -> GameIngestionStatus:
    """주어진 game_id의 수집 파이프라인 전체 상태를 집계한다.

    존재하지 않는 game_id는 None Game 조회로 HTTPException 전에 처리된다.
    라우트 핸들러에서 404를 발생시켜야 한다.

    Args:
        session: 활성 SQLAlchemy 세션.
        game_id: Game 테이블 PK.

    Returns:
        GameIngestionStatus.

    Raises:
        ValueError: game_id에 해당하는 Game 행이 없는 경우.
    """
    game = session.get(Game, game_id)
    if game is None:
        raise ValueError(f"Game {game_id} not found")

    gd = game.game_date
    ext_id = game.external_id

    categories: tuple[CategoryStatus, ...] = (
        _build_daily_category_status(session, game_date=gd, category="schedule"),
        _build_daily_category_status(session, game_date=gd, category="roster"),
        _build_daily_category_status(session, game_date=gd, category="player_stats"),
        _build_stat_snapshot_status(session, game_date=gd),
        _build_lineup_status(session, game_id=game_id, game_external_id=ext_id),
        _build_evaluation_status(session, game_id=game_id, game_external_id=ext_id),
        _build_box_score_status(session, game_id=game_id, game_external_id=ext_id),
        _build_postgame_review_status(session, game_id=game_id, game_external_id=ext_id),
    )

    return GameIngestionStatus(
        game_id=game_id,
        game_external_id=ext_id,
        game_date=gd,
        categories=categories,
    )


def list_recent_ingestion_runs(session: Session, *, limit: int = 50) -> list[IngestionRun]:
    """최근 IngestionRun 목록을 생성 일시 내림차순으로 반환한다.

    Args:
        session: 활성 SQLAlchemy 세션.
        limit: 최대 반환 개수 (1–500).

    Returns:
        IngestionRun 목록.
    """
    return list(
        session.execute(
            select(IngestionRun).order_by(IngestionRun.created_at.desc()).limit(limit)
        ).scalars()
    )
