"""Persistence helpers for raw ingestion payloads."""

from __future__ import annotations

import hashlib

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.snapshot import RawIngestionPayload
from app.schemas.ingestion import RawPayloadCreate

__all__ = ["get_raw_payload", "save_raw_payload"]


def _compute_payload_hash(raw_body: str) -> str:
    """SHA-256 of the raw body bytes. Used as the content fingerprint."""
    return hashlib.sha256(raw_body.encode("utf-8")).hexdigest()


def save_raw_payload(
    session: Session, payload: RawPayloadCreate
) -> tuple[RawIngestionPayload, bool]:
    """Idempotent INSERT keyed by (source_name, source_url, payload_hash).

    Args:
        session: Active SQLAlchemy session. Caller controls the transaction.
        payload: Validated input from a collector.

    Returns:
        Tuple of (row, created). Duplicate calls return the existing row with
        created=False. No commit is issued — caller flushes/commits as needed.
    """
    payload_hash = _compute_payload_hash(payload.raw_body)
    existing = session.execute(
        select(RawIngestionPayload).where(
            RawIngestionPayload.source_name == payload.source_name,
            RawIngestionPayload.source_url == payload.source_url,
            RawIngestionPayload.payload_hash == payload_hash,
        )
    ).scalar_one_or_none()
    if existing is not None:
        return existing, False
    row = RawIngestionPayload(
        ingestion_run_id=payload.ingestion_run_id,
        category=payload.category.value,
        source_name=payload.source_name,
        source_url=payload.source_url,
        fetched_at=payload.fetched_at,
        content_type=payload.content_type,
        payload_hash=payload_hash,
        raw_body=payload.raw_body,
    )
    session.add(row)
    session.flush()
    return row, True


def get_raw_payload(session: Session, raw_payload_id: int) -> RawIngestionPayload:
    """Return the payload row by id.

    Args:
        session: Active SQLAlchemy session.
        raw_payload_id: Primary key of the target row.

    Returns:
        The matching RawIngestionPayload row.

    Raises:
        sqlalchemy.exc.NoResultFound: If no row with that id exists.
    """
    return session.execute(
        select(RawIngestionPayload).where(RawIngestionPayload.id == raw_payload_id)
    ).scalar_one()
