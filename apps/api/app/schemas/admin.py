"""어드민 API용 Pydantic 응답 스키마."""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict


class IngestionRunSummary(BaseModel):
    """IngestionRun 한 행의 요약 정보."""

    model_config = ConfigDict(frozen=True)

    id: int
    source: str
    status: str  # "pending" | "running" | "completed" | "failed"
    started_at: datetime | None
    finished_at: datetime | None
    error_message: str | None
    created_at: datetime


class IngestionRunListResponse(BaseModel):
    """GET /api/admin/ingestion-runs 응답."""

    model_config = ConfigDict(frozen=True)

    runs: list[IngestionRunSummary]


class CategoryStatusResponse(BaseModel):
    """단일 카테고리의 수집 파이프라인 상태."""

    model_config = ConfigDict(frozen=True)

    category: str
    status: Literal["waiting", "collected", "normalized", "complete", "failed", "needs_review"]
    raw_payload_id: int | None
    snapshot_id: int | None
    run_id: int | None
    error_message: str | None


class GameIngestionStatusResponse(BaseModel):
    """GET /api/admin/games/{game_id}/ingestion-status 응답."""

    model_config = ConfigDict(frozen=True)

    game_id: int
    game_external_id: str
    game_date: date
    categories: list[CategoryStatusResponse]
