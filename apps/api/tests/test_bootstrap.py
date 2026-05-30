"""Tests for the bootstrap job (schema migration + reference-data seeding)."""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401 — registers models with Base.metadata
from app.db.base import Base
from app.ingestion.game_id import TEAM_CODES
from app.jobs.bootstrap import run_bootstrap, seed_reference_data


def test_seed_reference_data_is_idempotent() -> None:
    """First call seeds all teams + a model version; a second call adds nothing."""
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)

    with Session(engine) as s:
        first = seed_reference_data(s)
        s.commit()
    with Session(engine) as s:
        second = seed_reference_data(s)
        s.commit()

    assert first.teams_created == len(TEAM_CODES)
    assert second.teams_created == 0
    assert first.model_version_id == second.model_version_id

    with Session(engine) as s:
        team_count = s.execute(text("SELECT COUNT(*) FROM teams")).scalar_one()
    assert team_count == len(TEAM_CODES)


def test_run_bootstrap_creates_schema_and_seeds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """run_bootstrap migrates a fresh DB and seeds reference data, idempotently."""
    db_file = tmp_path / "boot.db"
    monkeypatch.setenv("KBO_DATABASE_URL", f"sqlite:///{db_file}")

    first = run_bootstrap()
    assert first.teams_created == len(TEAM_CODES)
    assert first.model_version_id > 0

    engine = create_engine(f"sqlite:///{db_file}")
    with engine.connect() as conn:
        assert conn.execute(text("SELECT COUNT(*) FROM teams")).scalar_one() == len(TEAM_CODES)
    engine.dispose()

    second = run_bootstrap()
    assert second.teams_created == 0
    assert second.model_version_id == first.model_version_id
