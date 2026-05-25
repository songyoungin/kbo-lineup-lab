"""IngestionRun get-or-create helper shared across all pipeline jobs."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.snapshot import IngestionRun


def get_or_create_ingestion_run(session: Session, *, source: str) -> IngestionRun:
    """source 문자열을 기준으로 IngestionRun을 조회하거나 새로 생성한다.

    IngestionRun.source에 UNIQUE 제약이 있으므로 소스 문자열이 자연키 역할을 한다.
    이미 존재하는 경우 기존 행을 반환하고, 없으면 status='pending'으로 새 행을 생성한다.
    커밋은 호출자가 담당한다.

    Args:
        session: 활성 SQLAlchemy 세션.
        source: 파이프라인 소스 식별자 (예: "pipeline:ingest-daily:2026-05-25").

    Returns:
        기존 또는 새로 생성된 IngestionRun.
    """
    existing = session.execute(
        select(IngestionRun).where(IngestionRun.source == source)
    ).scalar_one_or_none()
    if existing is not None:
        return existing
    run = IngestionRun(source=source, status="pending")
    session.add(run)
    session.flush()
    return run
