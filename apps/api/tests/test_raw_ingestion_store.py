"""Tests for raw ingestion payload store.

Verifies:
- First call creates a row and returns created=True
- Duplicate calls return existing row with created=False (no new DB row)
- Payload hash is SHA-256 of the raw body
- Raw body is preserved byte-exactly, including tricky content
- Same body at different URLs creates separate rows
- Same URL with different bodies creates separate rows
- get_raw_payload returns stored row
- get_raw_payload raises NoResultFound for missing id
- Naive fetched_at raises ValidationError
- All PayloadCategory values are accepted
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterator
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError
from sqlalchemy import create_engine
from sqlalchemy.exc import NoResultFound
from sqlalchemy.orm import Session, sessionmaker

import app.models  # noqa: F401 — registers all models with Base.metadata
from app.db.base import Base
from app.ingestion.raw_store import get_raw_payload, save_raw_payload
from app.ingestion.types import PayloadCategory
from app.models.snapshot import IngestionRun, RawIngestionPayload
from app.schemas.ingestion import RawPayloadCreate

# ---------------------------------------------------------------------------
# Session fixture
# ---------------------------------------------------------------------------


@pytest.fixture
def session() -> Iterator[Session]:
    """In-memory SQLite session with full schema."""
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine)() as s:
        yield s
    engine.dispose()


# ---------------------------------------------------------------------------
# Seed helpers
# ---------------------------------------------------------------------------

FETCHED_AT = datetime(2026, 5, 25, 10, 0, 0, tzinfo=UTC)


def _seed_run(session: Session) -> int:
    """Insert a minimal IngestionRun and return its id."""
    run = IngestionRun(source="test-source", status="running")
    session.add(run)
    session.flush()
    return run.id


def _make_payload(
    run_id: int,
    *,
    raw_body: str = "<html>page content</html>",
    source_url: str = "https://kbo.or.kr/schedule",
    category: PayloadCategory = PayloadCategory.SCHEDULE,
) -> RawPayloadCreate:
    return RawPayloadCreate(
        ingestion_run_id=run_id,
        category=category,
        source_name="kbo_official",
        source_url=source_url,
        fetched_at=FETCHED_AT,
        content_type="text/html; charset=utf-8",
        raw_body=raw_body,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_save_creates_row(session: Session) -> None:
    """First call returns created=True and populates the row correctly."""
    run_id = _seed_run(session)
    payload = _make_payload(run_id)

    row, created = save_raw_payload(session, payload)

    assert created is True
    assert row.id is not None
    assert row.ingestion_run_id == run_id
    assert row.category == PayloadCategory.SCHEDULE.value
    assert row.source_name == "kbo_official"
    assert row.source_url == "https://kbo.or.kr/schedule"
    assert row.content_type == "text/html; charset=utf-8"
    assert row.raw_body == "<html>page content</html>"
    assert len(row.payload_hash) == 64  # SHA-256 hex digest


def test_save_is_idempotent_on_duplicate(session: Session) -> None:
    """Second call with identical body returns same row id and created=False; no new DB row."""
    run_id = _seed_run(session)
    payload = _make_payload(run_id)

    row1, created1 = save_raw_payload(session, payload)
    row2, created2 = save_raw_payload(session, payload)

    assert created1 is True
    assert created2 is False
    assert row1.id == row2.id

    all_rows = session.query(RawIngestionPayload).all()
    assert len(all_rows) == 1


def test_payload_hash_computed_from_raw_body(session: Session) -> None:
    """Row's payload_hash matches SHA-256 of the raw body."""
    run_id = _seed_run(session)
    body = "<html>test</html>"
    payload = _make_payload(run_id, raw_body=body)

    row, _ = save_raw_payload(session, payload)

    expected = hashlib.sha256(body.encode("utf-8")).hexdigest()
    assert row.payload_hash == expected


def test_raw_body_preserved_exactly(session: Session) -> None:
    """Round-trip preserves tricky content byte-identically."""
    run_id = _seed_run(session)
    # Tricky body: HTML with <script>, JSON-like unicode, mixed whitespace
    tricky = '<html><script>alert("XSS")</script>\t\n  中文 éàü  {"key": "�"}</html>'
    payload = _make_payload(run_id, raw_body=tricky)

    row, _ = save_raw_payload(session, payload)
    session.expire(row)

    fetched = session.get(RawIngestionPayload, row.id)
    assert fetched is not None
    assert fetched.raw_body == tricky


def test_different_url_same_body_creates_two_rows(session: Session) -> None:
    """Same body fetched from two different URLs creates separate rows."""
    run_id = _seed_run(session)
    body = "<html>same</html>"
    p1 = _make_payload(run_id, raw_body=body, source_url="https://kbo.or.kr/schedule")
    p2 = _make_payload(run_id, raw_body=body, source_url="https://kbo.or.kr/roster")

    row1, c1 = save_raw_payload(session, p1)
    row2, c2 = save_raw_payload(session, p2)

    assert c1 is True
    assert c2 is True
    assert row1.id != row2.id
    assert session.query(RawIngestionPayload).count() == 2


def test_different_body_same_url_creates_two_rows(session: Session) -> None:
    """Same URL but different body content → separate rows."""
    run_id = _seed_run(session)
    url = "https://kbo.or.kr/schedule"
    p1 = _make_payload(run_id, raw_body="<html>day1</html>", source_url=url)
    p2 = _make_payload(run_id, raw_body="<html>day2</html>", source_url=url)

    row1, c1 = save_raw_payload(session, p1)
    row2, c2 = save_raw_payload(session, p2)

    assert c1 is True
    assert c2 is True
    assert row1.id != row2.id
    assert session.query(RawIngestionPayload).count() == 2


def test_get_raw_payload_returns_stored_row(session: Session) -> None:
    """get_raw_payload returns the row created by save_raw_payload."""
    run_id = _seed_run(session)
    payload = _make_payload(run_id)
    saved_row, _ = save_raw_payload(session, payload)

    fetched = get_raw_payload(session, saved_row.id)

    assert fetched.id == saved_row.id
    assert fetched.raw_body == payload.raw_body


def test_get_raw_payload_raises_for_missing_id(session: Session) -> None:
    """get_raw_payload raises NoResultFound when the id does not exist."""
    with pytest.raises(NoResultFound):
        get_raw_payload(session, 99999)


def test_save_rejects_naive_fetched_at(session: Session) -> None:
    """Pydantic raises ValidationError for a naive (tz-unaware) fetched_at."""
    run_id = _seed_run(session)
    with pytest.raises(ValidationError):
        RawPayloadCreate(
            ingestion_run_id=run_id,
            category=PayloadCategory.SCHEDULE,
            source_name="kbo_official",
            source_url="https://kbo.or.kr/schedule",
            fetched_at=datetime(2026, 5, 25, 10, 0, 0),  # naive — no tzinfo
            content_type="text/html",
            raw_body="<html/>",
        )


@pytest.mark.parametrize("category", list(PayloadCategory))
def test_save_supports_all_categories(session: Session, category: PayloadCategory) -> None:
    """All five PayloadCategory values can be stored and round-tripped."""
    run_id = _seed_run(session)
    payload = RawPayloadCreate(
        ingestion_run_id=run_id,
        category=category,
        source_name="kbo_official",
        source_url=f"https://kbo.or.kr/{category.value}",
        fetched_at=FETCHED_AT,
        content_type="application/json",
        raw_body=f'{{"category": "{category.value}"}}',
    )

    row, created = save_raw_payload(session, payload)

    assert created is True
    assert row.category == category.value
