"""Tests for get_or_create_evaluation_run idempotency and manifest hashing.

Verifies:
- First call creates a row with status='pending', populated manifest and hash
- Second call with same 6-tuple returns the same row (no duplicate)
- Different cutoff or model_version creates a distinct new row
- build_manifest + hash_manifest are deterministic
- Any key change in inputs changes the hash
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

import app.models  # noqa: F401 — registers all models with Base.metadata
from app.db.base import Base
from app.models.evaluation import LineupEvaluationRun, ModelVersion
from app.models.game import Game
from app.models.player import Player
from app.models.snapshot import (
    ActualLineupSnapshot,
    IngestionRun,
    PlayerStatSnapshotRow,
    StatSnapshot,
)
from app.models.team import Team
from app.services.evaluation_runs import get_or_create_evaluation_run
from app.services.run_manifest import build_manifest, canonical_json, hash_manifest

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


CUTOFF = datetime(2026, 4, 15, 9, 0, 0, tzinfo=UTC)
CUTOFF_ALT = datetime(2026, 4, 15, 10, 0, 0, tzinfo=UTC)


def _seed_all(session: Session) -> dict[str, int]:
    """Insert minimal rows required for evaluation run creation.

    Returns a dict with IDs: team_id, game_id, stat_snapshot_id,
    lineup_snapshot_id, model_version_id.
    """
    team = Team(code="LGS", name="LG Twins")
    opp = Team(code="OPPS", name="Opponent")
    session.add_all([team, opp])
    session.flush()

    game = Game(
        external_id="G-IDM-001",
        home_team_id=team.id,
        away_team_id=opp.id,
        game_date=date(2026, 4, 15),
    )
    session.add(game)
    session.flush()

    player = Player(team_id=team.id, external_id="LGS-P001", name="Lee", position="CF")
    session.add(player)
    session.flush()

    run = IngestionRun(source="seed-idm", status="completed")
    session.add(run)
    session.flush()

    stat_snap = StatSnapshot(
        ingestion_run_id=run.id,
        snapshot_at=datetime(2026, 4, 15, 7, 0, tzinfo=UTC),
        content_hash="idm-stat-hash",
    )
    session.add(stat_snap)
    session.flush()

    session.add(
        PlayerStatSnapshotRow(
            snapshot_id=stat_snap.id,
            player_id=player.id,
            stats_json={"avg": 0.300},
        )
    )
    session.flush()

    lineup_snap = ActualLineupSnapshot(
        game_id=game.id,
        team_id=team.id,
        ingestion_run_id=run.id,
        announced_at=datetime(2026, 4, 15, 8, 0, tzinfo=UTC),
        content_hash="idm-lineup-hash",
    )
    session.add(lineup_snap)
    session.flush()

    model_ver = ModelVersion(name="test-model", version="v1", model_id="anthropic/claude-test")
    session.add(model_ver)
    session.flush()

    return {
        "team_id": team.id,
        "game_id": game.id,
        "stat_snapshot_id": stat_snap.id,
        "lineup_snapshot_id": lineup_snap.id,
        "model_version_id": model_ver.id,
    }


# ---------------------------------------------------------------------------
# get_or_create_evaluation_run tests
# ---------------------------------------------------------------------------


def test_get_or_create_inserts_when_absent(session: Session) -> None:
    """First call creates a row with status='pending' and populated manifest/hash."""
    ids = _seed_all(session)

    run = get_or_create_evaluation_run(
        session,
        game_id=ids["game_id"],
        team_id=ids["team_id"],
        evaluation_cutoff_at=CUTOFF,
        stat_snapshot_id=ids["stat_snapshot_id"],
        lineup_snapshot_id=ids["lineup_snapshot_id"],
        model_version_id=ids["model_version_id"],
    )

    assert run.id is not None
    assert run.status == "pending"
    assert run.input_manifest_json is not None
    assert run.input_hash is not None
    assert len(run.input_hash) == 64  # SHA-256 hex digest
    assert run.output_hash is None  # not set until model execution
    assert run.started_at is None


def test_get_or_create_returns_existing_when_present(session: Session) -> None:
    """Second call with identical 6-tuple returns the SAME row id, no duplicate."""
    ids = _seed_all(session)

    run1 = get_or_create_evaluation_run(
        session,
        game_id=ids["game_id"],
        team_id=ids["team_id"],
        evaluation_cutoff_at=CUTOFF,
        stat_snapshot_id=ids["stat_snapshot_id"],
        lineup_snapshot_id=ids["lineup_snapshot_id"],
        model_version_id=ids["model_version_id"],
    )
    run2 = get_or_create_evaluation_run(
        session,
        game_id=ids["game_id"],
        team_id=ids["team_id"],
        evaluation_cutoff_at=CUTOFF,
        stat_snapshot_id=ids["stat_snapshot_id"],
        lineup_snapshot_id=ids["lineup_snapshot_id"],
        model_version_id=ids["model_version_id"],
    )

    assert run1.id == run2.id

    # Confirm only one row exists in DB
    rows = session.query(LineupEvaluationRun).all()
    assert len(rows) == 1


def test_different_cutoff_creates_new_run(session: Session) -> None:
    """Different evaluation_cutoff_at must produce a distinct new row."""
    ids = _seed_all(session)

    run1 = get_or_create_evaluation_run(
        session,
        game_id=ids["game_id"],
        team_id=ids["team_id"],
        evaluation_cutoff_at=CUTOFF,
        stat_snapshot_id=ids["stat_snapshot_id"],
        lineup_snapshot_id=ids["lineup_snapshot_id"],
        model_version_id=ids["model_version_id"],
    )
    run2 = get_or_create_evaluation_run(
        session,
        game_id=ids["game_id"],
        team_id=ids["team_id"],
        evaluation_cutoff_at=CUTOFF_ALT,
        stat_snapshot_id=ids["stat_snapshot_id"],
        lineup_snapshot_id=ids["lineup_snapshot_id"],
        model_version_id=ids["model_version_id"],
    )

    assert run1.id != run2.id

    rows = session.query(LineupEvaluationRun).all()
    assert len(rows) == 2


def test_different_model_version_creates_new_run(session: Session) -> None:
    """Different model_version_id must produce a distinct new row."""
    ids = _seed_all(session)

    # Add a second model version
    mv2 = ModelVersion(name="test-model", version="v2", model_id="anthropic/claude-test-v2")
    session.add(mv2)
    session.flush()

    run1 = get_or_create_evaluation_run(
        session,
        game_id=ids["game_id"],
        team_id=ids["team_id"],
        evaluation_cutoff_at=CUTOFF,
        stat_snapshot_id=ids["stat_snapshot_id"],
        lineup_snapshot_id=ids["lineup_snapshot_id"],
        model_version_id=ids["model_version_id"],
    )
    run2 = get_or_create_evaluation_run(
        session,
        game_id=ids["game_id"],
        team_id=ids["team_id"],
        evaluation_cutoff_at=CUTOFF,
        stat_snapshot_id=ids["stat_snapshot_id"],
        lineup_snapshot_id=ids["lineup_snapshot_id"],
        model_version_id=mv2.id,
    )

    assert run1.id != run2.id


# ---------------------------------------------------------------------------
# Manifest and hash tests
# ---------------------------------------------------------------------------


def test_manifest_hash_deterministic(session: Session) -> None:
    """Calling build_manifest + hash_manifest twice with identical inputs yields the same hash."""
    ids = _seed_all(session)

    manifest1 = build_manifest(
        game_id=ids["game_id"],
        team_id=ids["team_id"],
        evaluation_cutoff_at=CUTOFF,
        stat_snapshot_id=ids["stat_snapshot_id"],
        lineup_snapshot_id=ids["lineup_snapshot_id"],
        model_version_id=ids["model_version_id"],
    )
    manifest2 = build_manifest(
        game_id=ids["game_id"],
        team_id=ids["team_id"],
        evaluation_cutoff_at=CUTOFF,
        stat_snapshot_id=ids["stat_snapshot_id"],
        lineup_snapshot_id=ids["lineup_snapshot_id"],
        model_version_id=ids["model_version_id"],
    )

    assert hash_manifest(manifest1) == hash_manifest(manifest2)


def test_manifest_hash_changes_when_game_id_changes(session: Session) -> None:
    """Bumping game_id changes the hash."""
    ids = _seed_all(session)

    h1 = hash_manifest(
        build_manifest(
            game_id=ids["game_id"],
            team_id=ids["team_id"],
            evaluation_cutoff_at=CUTOFF,
            stat_snapshot_id=ids["stat_snapshot_id"],
            lineup_snapshot_id=ids["lineup_snapshot_id"],
            model_version_id=ids["model_version_id"],
        )
    )
    h2 = hash_manifest(
        build_manifest(
            game_id=ids["game_id"] + 1,
            team_id=ids["team_id"],
            evaluation_cutoff_at=CUTOFF,
            stat_snapshot_id=ids["stat_snapshot_id"],
            lineup_snapshot_id=ids["lineup_snapshot_id"],
            model_version_id=ids["model_version_id"],
        )
    )
    assert h1 != h2


def test_manifest_hash_changes_when_team_id_changes(session: Session) -> None:
    """Bumping team_id changes the hash."""
    ids = _seed_all(session)

    h1 = hash_manifest(
        build_manifest(
            game_id=ids["game_id"],
            team_id=ids["team_id"],
            evaluation_cutoff_at=CUTOFF,
            stat_snapshot_id=ids["stat_snapshot_id"],
            lineup_snapshot_id=ids["lineup_snapshot_id"],
            model_version_id=ids["model_version_id"],
        )
    )
    h2 = hash_manifest(
        build_manifest(
            game_id=ids["game_id"],
            team_id=ids["team_id"] + 1,
            evaluation_cutoff_at=CUTOFF,
            stat_snapshot_id=ids["stat_snapshot_id"],
            lineup_snapshot_id=ids["lineup_snapshot_id"],
            model_version_id=ids["model_version_id"],
        )
    )
    assert h1 != h2


def test_manifest_hash_changes_when_cutoff_changes(session: Session) -> None:
    """Bumping evaluation_cutoff_at changes the hash."""
    ids = _seed_all(session)

    h1 = hash_manifest(
        build_manifest(
            game_id=ids["game_id"],
            team_id=ids["team_id"],
            evaluation_cutoff_at=CUTOFF,
            stat_snapshot_id=ids["stat_snapshot_id"],
            lineup_snapshot_id=ids["lineup_snapshot_id"],
            model_version_id=ids["model_version_id"],
        )
    )
    h2 = hash_manifest(
        build_manifest(
            game_id=ids["game_id"],
            team_id=ids["team_id"],
            evaluation_cutoff_at=CUTOFF_ALT,
            stat_snapshot_id=ids["stat_snapshot_id"],
            lineup_snapshot_id=ids["lineup_snapshot_id"],
            model_version_id=ids["model_version_id"],
        )
    )
    assert h1 != h2


def test_manifest_hash_changes_when_stat_snapshot_id_changes(session: Session) -> None:
    """Bumping stat_snapshot_id changes the hash."""
    ids = _seed_all(session)

    h1 = hash_manifest(
        build_manifest(
            game_id=ids["game_id"],
            team_id=ids["team_id"],
            evaluation_cutoff_at=CUTOFF,
            stat_snapshot_id=ids["stat_snapshot_id"],
            lineup_snapshot_id=ids["lineup_snapshot_id"],
            model_version_id=ids["model_version_id"],
        )
    )
    h2 = hash_manifest(
        build_manifest(
            game_id=ids["game_id"],
            team_id=ids["team_id"],
            evaluation_cutoff_at=CUTOFF,
            stat_snapshot_id=ids["stat_snapshot_id"] + 1,
            lineup_snapshot_id=ids["lineup_snapshot_id"],
            model_version_id=ids["model_version_id"],
        )
    )
    assert h1 != h2


def test_manifest_hash_changes_when_lineup_snapshot_id_changes(session: Session) -> None:
    """Bumping lineup_snapshot_id changes the hash."""
    ids = _seed_all(session)

    h1 = hash_manifest(
        build_manifest(
            game_id=ids["game_id"],
            team_id=ids["team_id"],
            evaluation_cutoff_at=CUTOFF,
            stat_snapshot_id=ids["stat_snapshot_id"],
            lineup_snapshot_id=ids["lineup_snapshot_id"],
            model_version_id=ids["model_version_id"],
        )
    )
    h2 = hash_manifest(
        build_manifest(
            game_id=ids["game_id"],
            team_id=ids["team_id"],
            evaluation_cutoff_at=CUTOFF,
            stat_snapshot_id=ids["stat_snapshot_id"],
            lineup_snapshot_id=ids["lineup_snapshot_id"] + 1,
            model_version_id=ids["model_version_id"],
        )
    )
    assert h1 != h2


def test_manifest_hash_changes_when_model_version_id_changes(session: Session) -> None:
    """Bumping model_version_id changes the hash."""
    ids = _seed_all(session)

    h1 = hash_manifest(
        build_manifest(
            game_id=ids["game_id"],
            team_id=ids["team_id"],
            evaluation_cutoff_at=CUTOFF,
            stat_snapshot_id=ids["stat_snapshot_id"],
            lineup_snapshot_id=ids["lineup_snapshot_id"],
            model_version_id=ids["model_version_id"],
        )
    )
    h2 = hash_manifest(
        build_manifest(
            game_id=ids["game_id"],
            team_id=ids["team_id"],
            evaluation_cutoff_at=CUTOFF,
            stat_snapshot_id=ids["stat_snapshot_id"],
            lineup_snapshot_id=ids["lineup_snapshot_id"],
            model_version_id=ids["model_version_id"] + 1,
        )
    )
    assert h1 != h2


def test_get_or_create_stores_model_config(session: Session) -> None:
    """model_config_json is stored when model_config is provided."""
    ids = _seed_all(session)

    run = get_or_create_evaluation_run(
        session,
        game_id=ids["game_id"],
        team_id=ids["team_id"],
        evaluation_cutoff_at=CUTOFF,
        stat_snapshot_id=ids["stat_snapshot_id"],
        lineup_snapshot_id=ids["lineup_snapshot_id"],
        model_version_id=ids["model_version_id"],
        model_config={"temperature": 0.7, "max_tokens": 1024},
    )

    assert run.model_config_json == {"temperature": 0.7, "max_tokens": 1024}
    # model_config must also appear in the manifest
    assert run.input_manifest_json is not None
    assert run.input_manifest_json.get("model_config") == {"temperature": 0.7, "max_tokens": 1024}


def test_hash_manifest_rejects_nan_float() -> None:
    """canonical_json/hash_manifest must reject NaN floats (allow_nan=False)."""
    with pytest.raises(ValueError):
        hash_manifest({"x": float("nan")})


def test_hash_manifest_rejects_infinity_float() -> None:
    """canonical_json/hash_manifest must reject Infinity floats (allow_nan=False)."""
    with pytest.raises(ValueError):
        hash_manifest({"x": float("inf")})


def test_canonical_json_rejects_nan_float() -> None:
    """canonical_json must reject NaN directly (regression for the allow_nan guard)."""
    with pytest.raises(ValueError):
        canonical_json({"x": float("nan")})


def test_get_or_create_returns_existing_for_non_utc_cutoff(session: Session) -> None:
    """Regression: EST cutoff (UTC-5) representing same instant as a prior UTC
    cutoff must return the existing row, not create a duplicate.

    Without UTC normalization in the selector + insert path, the SELECT clause
    compares stored UTC text vs the new EST wall-clock text and misses the row,
    creating a duplicate that then fails the UNIQUE constraint at flush.
    """
    ids = _seed_all(session)

    # First call: UTC cutoff at 2026-04-15T08:00:00Z
    cutoff_utc = datetime(2026, 4, 15, 8, 0, 0, tzinfo=UTC)
    run1 = get_or_create_evaluation_run(
        session,
        game_id=ids["game_id"],
        team_id=ids["team_id"],
        evaluation_cutoff_at=cutoff_utc,
        stat_snapshot_id=ids["stat_snapshot_id"],
        lineup_snapshot_id=ids["lineup_snapshot_id"],
        model_version_id=ids["model_version_id"],
    )

    # Second call: EST cutoff representing the SAME instant (03:00-05:00)
    est = timezone(timedelta(hours=-5))
    cutoff_est = datetime(2026, 4, 15, 3, 0, 0, tzinfo=est)
    run2 = get_or_create_evaluation_run(
        session,
        game_id=ids["game_id"],
        team_id=ids["team_id"],
        evaluation_cutoff_at=cutoff_est,
        stat_snapshot_id=ids["stat_snapshot_id"],
        lineup_snapshot_id=ids["lineup_snapshot_id"],
        model_version_id=ids["model_version_id"],
    )

    assert run1.id == run2.id

    # Only one row exists
    rows = session.query(LineupEvaluationRun).all()
    assert len(rows) == 1
