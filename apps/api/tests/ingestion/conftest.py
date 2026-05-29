"""Shared fixtures for ingestion tests: in-memory session, mock HttpClient
builder, and a loader for captured source JSON fixtures."""

from __future__ import annotations

import json  # noqa: F401 — used by later tasks
from collections.abc import Callable, Iterator
from pathlib import Path

import httpx
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

import app.models  # noqa: F401 — registers models with Base.metadata
from app.db.base import Base
from app.ingestion.http_client import HttpClient

FIXTURE_DIR = Path(__file__).resolve().parent.parent / "fixtures" / "sources"


@pytest.fixture
def session() -> Iterator[Session]:
    """Create an in-memory SQLite session and dispose it after the test."""
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine)() as s:
        yield s
    engine.dispose()


@pytest.fixture
def mock_http() -> Callable[[Callable[[httpx.Request], httpx.Response]], HttpClient]:
    """Factory fixture: takes a MockTransport handler, returns an HttpClient."""

    def build(handler: Callable[[httpx.Request], httpx.Response]) -> HttpClient:
        transport = httpx.MockTransport(handler)
        return HttpClient(client=httpx.Client(transport=transport), retry_backoff=(0.0,))

    return build


@pytest.fixture
def load_source() -> Callable[[str], str]:
    """Loader fixture that reads file contents from tests/fixtures/sources/."""

    def load(relpath: str) -> str:
        return (FIXTURE_DIR / relpath).read_text(encoding="utf-8")

    return load
