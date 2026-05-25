"""어드민용 수집 파이프라인 상태 조회 라우트."""

from __future__ import annotations

from dataclasses import asdict

from fastapi import APIRouter, HTTPException, Query

from app.api.deps import SessionDep
from app.schemas.admin import (
    CategoryStatusResponse,
    GameIngestionStatusResponse,
    IngestionRunListResponse,
    IngestionRunSummary,
)
from app.services.ingestion_status import (
    build_game_ingestion_status,
    list_recent_ingestion_runs,
)

router = APIRouter()


@router.get("/ingestion-runs", response_model=IngestionRunListResponse)
def list_runs(
    session: SessionDep,
    limit: int = Query(50, ge=1, le=500),
) -> IngestionRunListResponse:
    """최근 IngestionRun 목록을 생성 일시 내림차순으로 반환한다."""
    runs = list_recent_ingestion_runs(session, limit=limit)
    return IngestionRunListResponse(
        runs=[IngestionRunSummary.model_validate(r, from_attributes=True) for r in runs]
    )


@router.get("/games/{game_id}/ingestion-status", response_model=GameIngestionStatusResponse)
def game_status(game_id: int, session: SessionDep) -> GameIngestionStatusResponse:
    """주어진 게임의 수집 파이프라인 전체 상태를 반환한다."""
    try:
        status = build_game_ingestion_status(session, game_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    return GameIngestionStatusResponse(
        game_id=status.game_id,
        game_external_id=status.game_external_id,
        game_date=status.game_date,
        categories=[CategoryStatusResponse(**asdict(c)) for c in status.categories],
    )
