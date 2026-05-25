"""Pydantic schemas for the raw ingestion payload contract."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, field_validator

from app.ingestion.types import PayloadCategory
from app.util.time import to_utc

__all__ = ["RawPayloadCreate"]


class RawPayloadCreate(BaseModel):
    """Input payload for save_raw_payload — what a collector hands in."""

    model_config = ConfigDict(frozen=True)

    ingestion_run_id: int
    category: PayloadCategory
    source_name: str
    source_url: str
    fetched_at: datetime
    content_type: str
    raw_body: str

    @field_validator("fetched_at")
    @classmethod
    def _ensure_tz_aware(cls, v: datetime) -> datetime:
        """Normalize fetched_at to UTC; raises ValueError for naive datetimes."""
        return to_utc(v)

    @field_validator("source_url")
    @classmethod
    def _trim(cls, v: str) -> str:
        """Strip leading/trailing whitespace from the URL."""
        return v.strip()
