"""Tests for the opt-in X-API-Token guard on the admin/jobs routers."""

from collections.abc import Iterator

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app import models as _models  # noqa: F401 — registers all models with Base.metadata
from app.api.deps import get_session, require_api_token
from app.db.base import Base
from app.main import app

client = TestClient(app)


@pytest.fixture
def empty_db_client() -> Iterator[TestClient]:
    """A TestClient backed by a fresh in-memory DB with tables but no rows.

    Lets DB-backed routes return a deterministic 404 for a missing row,
    independent of whatever KBO_DATABASE_URL points at in the environment.
    """
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, class_=Session, expire_on_commit=False)

    def override() -> Iterator[Session]:
        with factory() as session:
            yield session

    app.dependency_overrides[get_session] = override
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


def test_guard_is_noop_when_env_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    """Without KBO_ADMIN_TOKEN the guard accepts any (or no) header."""
    monkeypatch.delenv("KBO_ADMIN_TOKEN", raising=False)
    require_api_token(None)
    require_api_token("anything")


def test_guard_accepts_matching_token(monkeypatch: pytest.MonkeyPatch) -> None:
    """A header equal to KBO_ADMIN_TOKEN passes."""
    monkeypatch.setenv("KBO_ADMIN_TOKEN", "sekrit")
    require_api_token("sekrit")


@pytest.mark.parametrize("sent", [None, "", "wrong"])
def test_guard_rejects_bad_token(monkeypatch: pytest.MonkeyPatch, sent: str | None) -> None:
    """A missing/empty/mismatched header raises 401 when the env is set."""
    monkeypatch.setenv("KBO_ADMIN_TOKEN", "sekrit")
    with pytest.raises(HTTPException) as exc_info:
        require_api_token(sent)
    assert exc_info.value.status_code == 401


def test_admin_route_401_without_token(monkeypatch: pytest.MonkeyPatch) -> None:
    """The admin router is wired to the guard."""
    monkeypatch.setenv("KBO_ADMIN_TOKEN", "sekrit")
    assert client.get("/api/admin/ingestion-runs").status_code == 401


def test_jobs_route_401_without_token(monkeypatch: pytest.MonkeyPatch) -> None:
    """The jobs router is wired to the guard."""
    monkeypatch.setenv("KBO_ADMIN_TOKEN", "sekrit")
    assert client.post("/api/jobs/replay-evaluation", json={}).status_code == 401


def test_games_route_stays_public(
    monkeypatch: pytest.MonkeyPatch, empty_db_client: TestClient
) -> None:
    """Read endpoints are not behind the guard even when the env is set.

    Uses a tables-but-empty DB so a missing game yields a clean 404 (not an
    auth rejection) deterministically in any environment.
    """
    monkeypatch.setenv("KBO_ADMIN_TOKEN", "sekrit")
    assert empty_db_client.get("/api/games/999999/pregame").status_code == 404
