"""Idempotent database bootstrap: schema migration + reference-data seeding.

`run_bootstrap` is the single entrypoint a fresh deployment runs once before
ingestion. It applies Alembic migrations against ``KBO_DATABASE_URL`` and then
seeds the static reference data (the 10 KBO teams and a default ModelVersion)
that ingestion and evaluation depend on. Every step is idempotent.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from alembic.config import Config
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

import app  # for locating alembic.ini relative to the installed package
from alembic import command
from app.ingestion.game_id import TEAM_CODES
from app.models.evaluation import ModelVersion
from app.models.team import Team

_DEFAULT_DATABASE_URL = "sqlite:///./kbo_lineup_lab.db"
_DEFAULT_MODEL_NAME = "heuristic-v1"
_DEFAULT_MODEL_VERSION = "v1"
_DEFAULT_MODEL_ID = "internal/lineup-score-v1"


@dataclass(frozen=True)
class BootstrapResult:
    """Outcome of a bootstrap run.

    Attributes:
        teams_created: Number of Team rows inserted this run (0 if all existed).
        model_version_id: PK of the existing-or-created default ModelVersion.
    """

    teams_created: int
    model_version_id: int


def _database_url() -> str:
    """Return the configured database URL (same source as app.db.session)."""
    return os.environ.get("KBO_DATABASE_URL", _DEFAULT_DATABASE_URL)


def _alembic_config() -> Config:
    """Build an Alembic Config pointing at the project's alembic.ini.

    Located by absolute path so the upgrade works regardless of cwd. env.py reads
    KBO_DATABASE_URL from the environment, so no URL is set here.
    """
    ini_path = Path(app.__file__).resolve().parent.parent / "alembic.ini"
    return Config(str(ini_path))


def upgrade_schema() -> None:
    """Apply all Alembic migrations (``upgrade head``) against KBO_DATABASE_URL."""
    command.upgrade(_alembic_config(), "head")


def seed_reference_data(session: Session) -> BootstrapResult:
    """Idempotently seed the 10 KBO teams and a default ModelVersion.

    Args:
        session: Active SQLAlchemy session (caller commits).

    Returns:
        BootstrapResult with the number of teams created and the model version id.
    """
    teams_created = 0
    for code, name in TEAM_CODES.items():
        existing = session.execute(select(Team).where(Team.code == code)).scalars().first()
        if existing is None:
            session.add(Team(code=code, name=name))
            teams_created += 1
    session.flush()

    model_version = (
        session.execute(
            select(ModelVersion).where(
                ModelVersion.name == _DEFAULT_MODEL_NAME,
                ModelVersion.version == _DEFAULT_MODEL_VERSION,
            )
        )
        .scalars()
        .first()
    )
    if model_version is None:
        model_version = ModelVersion(
            name=_DEFAULT_MODEL_NAME,
            version=_DEFAULT_MODEL_VERSION,
            model_id=_DEFAULT_MODEL_ID,
        )
        session.add(model_version)
        session.flush()

    return BootstrapResult(teams_created=teams_created, model_version_id=model_version.id)


def run_bootstrap() -> BootstrapResult:
    """Migrate the schema and seed reference data against KBO_DATABASE_URL.

    Builds its own engine from the current environment URL rather than reusing
    app.db.session.SessionLocal (which is bound at import time), so the target
    database always matches KBO_DATABASE_URL at call time.

    Returns:
        BootstrapResult summarising what was seeded.
    """
    upgrade_schema()

    url = _database_url()
    connect_args = {"check_same_thread": False} if url.startswith("sqlite") else {}
    engine = create_engine(url, connect_args=connect_args)
    try:
        with Session(engine) as session:
            result = seed_reference_data(session)
            session.commit()
        return result
    finally:
        engine.dispose()
