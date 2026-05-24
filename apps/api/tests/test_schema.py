"""Tests for the domain database schema.

Verifies:
- All 16 tables are created in an in-memory SQLite database
- Basic CRUD round-trips for top-level entities
- Idempotency UNIQUE constraint on lineup_evaluation_runs
- Required indexes exist on the expected columns
"""

from collections.abc import Iterator
from datetime import UTC, date, datetime

import pytest
from sqlalchemy import Engine, create_engine, inspect
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

# Import package to register all models with Base.metadata
import app.models  # noqa: F401
from app.db.base import Base
from app.models.evaluation import (
    LineupEvaluationRun,
    LineupEvaluationSummary,
    ModelVersion,
    RecommendedLineupRow,
)
from app.models.game import Game
from app.models.player import Player
from app.models.postgame import PostgameReviewRun, PostgameReviewSummary
from app.models.snapshot import (
    ActualLineupSnapshot,
    ActualLineupSnapshotRow,
    BoxScoreRow,
    BoxScoreSnapshot,
    IngestionRun,
    PlayerStatSnapshotRow,
    StatSnapshot,
)
from app.models.team import Team

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def engine() -> Iterator[Engine]:
    """In-memory SQLite engine with all tables created."""
    _engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(_engine)
    yield _engine
    _engine.dispose()


@pytest.fixture
def session(engine: Engine) -> Iterator[Session]:
    """Fresh session that rolls back after each test."""
    _SessionLocal = sessionmaker(bind=engine)
    _session = _SessionLocal()
    yield _session
    _session.rollback()
    _session.close()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _now() -> datetime:
    return datetime.now(UTC)


def _make_team(session: Session, code: str = "LG", name: str = "LG Twins") -> Team:
    team = Team(code=code, name=name)
    session.add(team)
    session.flush()
    return team


def _make_player(session: Session, team_id: int, ext: str = "P001") -> Player:
    player = Player(team_id=team_id, external_id=ext, name="Kim Min-jun", position="CF")
    session.add(player)
    session.flush()
    return player


def _make_ingestion_run(session: Session) -> IngestionRun:
    run = IngestionRun(source="statiz", status="completed")
    session.add(run)
    session.flush()
    return run


def _make_stat_snapshot(session: Session, run_id: int) -> StatSnapshot:
    snap = StatSnapshot(
        ingestion_run_id=run_id,
        snapshot_at=_now(),
        content_hash="a" * 64,
    )
    session.add(snap)
    session.flush()
    return snap


def _make_lineup_snapshot(
    session: Session,
    game_id: int,
    run_id: int,
    team_id: int,
    announced_at: datetime | None = None,
) -> ActualLineupSnapshot:
    snap = ActualLineupSnapshot(
        game_id=game_id,
        team_id=team_id,
        ingestion_run_id=run_id,
        announced_at=announced_at or _now(),
        content_hash="b" * 64,
    )
    session.add(snap)
    session.flush()
    return snap


def _make_model_version(
    session: Session, name: str = "Claude Opus 4", ver: str = "1.0.0"
) -> ModelVersion:
    mv = ModelVersion(
        name=name,
        version=ver,
        model_id="anthropic/claude-opus-4",
    )
    session.add(mv)
    session.flush()
    return mv


def _make_eval_run(
    session: Session,
    game_id: int,
    team_id: int,
    stat_snapshot_id: int,
    lineup_snapshot_id: int,
    model_version_id: int,
    cutoff: datetime | None = None,
) -> LineupEvaluationRun:
    run = LineupEvaluationRun(
        game_id=game_id,
        team_id=team_id,
        stat_snapshot_id=stat_snapshot_id,
        lineup_snapshot_id=lineup_snapshot_id,
        model_version_id=model_version_id,
        evaluation_cutoff_at=cutoff or _now(),
        status="completed",
    )
    session.add(run)
    session.flush()
    return run


# ---------------------------------------------------------------------------
# Table existence
# ---------------------------------------------------------------------------


EXPECTED_TABLES = {
    "teams",
    "players",
    "games",
    "ingestion_runs",
    "stat_snapshots",
    "player_stat_snapshot_rows",
    "actual_lineup_snapshots",
    "actual_lineup_snapshot_rows",
    "model_versions",
    "lineup_evaluation_runs",
    "recommended_lineup_rows",
    "lineup_evaluation_summaries",
    "box_score_snapshots",
    "box_score_rows",
    "postgame_review_runs",
    "postgame_review_summaries",
}


def test_all_tables_created(engine: Engine) -> None:
    """All 16 domain tables must be present after create_all."""
    inspector = inspect(engine)
    actual = set(inspector.get_table_names())
    missing = EXPECTED_TABLES - actual
    assert not missing, f"Missing tables: {missing}"


# ---------------------------------------------------------------------------
# CRUD round-trips for top-level entities
# ---------------------------------------------------------------------------


def test_team_crud(session: Session) -> None:
    _make_team(session)
    session.commit()
    fetched = session.query(Team).filter_by(code="LG").one()
    assert fetched.name == "LG Twins"


def test_player_crud(session: Session) -> None:
    team = _make_team(session, code="LGP", name="LG Twins Player Test")
    _make_player(session, team.id, ext="P_CRUD_001")
    session.commit()
    fetched = session.query(Player).filter_by(external_id="P_CRUD_001").one()
    assert fetched.position == "CF"
    assert fetched.team_id == team.id


def test_game_crud(session: Session) -> None:
    home = _make_team(session, code="LGG", name="LG Twins Game Test")
    away = _make_team(session, code="SSG", name="Samsung Lions Game Test")
    game = Game(
        external_id="G2026_CRUD",
        home_team_id=home.id,
        away_team_id=away.id,
        game_date=date(2026, 5, 25),
    )
    session.add(game)
    session.commit()
    fetched = session.query(Game).filter_by(external_id="G2026_CRUD").one()
    assert fetched.game_date == date(2026, 5, 25)


def test_model_version_crud(session: Session) -> None:
    _make_model_version(session)
    session.commit()
    fetched = session.query(ModelVersion).filter_by(version="1.0.0").one()
    assert fetched.model_id == "anthropic/claude-opus-4"


# ---------------------------------------------------------------------------
# Idempotency UNIQUE constraint on lineup_evaluation_runs
# ---------------------------------------------------------------------------


def test_lineup_evaluation_runs_idempotency_constraint(session: Session) -> None:
    """Inserting two rows with identical idempotency-key columns must raise IntegrityError."""
    home = _make_team(session, code="LG2", name="LG Twins 2")
    away = _make_team(session, code="SK2", name="SK Wyverns 2")
    game = Game(
        external_id="G_IDEM",
        home_team_id=home.id,
        away_team_id=away.id,
        game_date=date(2026, 5, 25),
    )
    session.add(game)
    session.flush()
    ir = _make_ingestion_run(session)
    ss = _make_stat_snapshot(session, ir.id)
    ls = _make_lineup_snapshot(session, game.id, ir.id, home.id)
    mv = _make_model_version(session, ver="2.0.0")
    cutoff = _now()

    _make_eval_run(session, game.id, home.id, ss.id, ls.id, mv.id, cutoff)
    session.commit()

    # Duplicate row with same idempotency key
    dup = LineupEvaluationRun(
        game_id=game.id,
        team_id=home.id,
        stat_snapshot_id=ss.id,
        lineup_snapshot_id=ls.id,
        model_version_id=mv.id,
        evaluation_cutoff_at=cutoff,
        status="pending",
    )
    session.add(dup)
    with pytest.raises(IntegrityError):
        session.flush()


# ---------------------------------------------------------------------------
# Additional UNIQUE constraint checks
# ---------------------------------------------------------------------------


def test_model_versions_name_version_unique(session: Session) -> None:
    """Two ModelVersion rows with same (name, version) must raise IntegrityError."""
    _make_model_version(session, name="DupModel", ver="9.9.9")
    session.commit()

    dup = ModelVersion(
        name="DupModel",
        version="9.9.9",
        model_id="anthropic/claude-opus-4",
    )
    session.add(dup)
    with pytest.raises(IntegrityError):
        session.flush()


def test_actual_lineup_snapshots_game_team_announced_unique(session: Session) -> None:
    """Two snapshots with the same (game, team, announced_at) must raise IntegrityError."""
    home = _make_team(session, code="LGU", name="LG Twins Unique")
    away = _make_team(session, code="SSU", name="Samsung Unique")
    game = Game(
        external_id="G_UNIQ_SNAP",
        home_team_id=home.id,
        away_team_id=away.id,
        game_date=date(2026, 5, 25),
    )
    session.add(game)
    session.flush()
    ir = _make_ingestion_run(session)
    announced = _now()
    _make_lineup_snapshot(session, game.id, ir.id, home.id, announced_at=announced)
    session.commit()

    dup = ActualLineupSnapshot(
        game_id=game.id,
        team_id=home.id,
        ingestion_run_id=ir.id,
        announced_at=announced,
        content_hash="z" * 64,
    )
    session.add(dup)
    with pytest.raises(IntegrityError):
        session.flush()


# ---------------------------------------------------------------------------
# Index checks
# ---------------------------------------------------------------------------


def _index_columns(engine: Engine, table: str) -> set[str]:
    """Return the set of column names covered by any index on the given table."""
    inspector = inspect(engine)
    cols: set[str] = set()
    for idx in inspector.get_indexes(table):
        # column_names may contain None for expression indexes; filter those out
        cols.update(c for c in idx["column_names"] if c is not None)
    return cols


def test_index_stat_snapshots_snapshot_at(engine: Engine) -> None:
    assert "snapshot_at" in _index_columns(engine, "stat_snapshots")


def test_index_actual_lineup_snapshots_announced_at(engine: Engine) -> None:
    assert "announced_at" in _index_columns(engine, "actual_lineup_snapshots")


def test_index_games_game_date(engine: Engine) -> None:
    assert "game_date" in _index_columns(engine, "games")


def test_index_ingestion_runs_status(engine: Engine) -> None:
    assert "status" in _index_columns(engine, "ingestion_runs")


def test_index_lineup_evaluation_runs_status(engine: Engine) -> None:
    assert "status" in _index_columns(engine, "lineup_evaluation_runs")


def test_index_box_score_snapshots_taken_at(engine: Engine) -> None:
    assert "taken_at" in _index_columns(engine, "box_score_snapshots")


# ---------------------------------------------------------------------------
# Related row creation (snapshots, evaluation chain)
# ---------------------------------------------------------------------------


def test_full_evaluation_chain(session: Session) -> None:
    """Create a complete evaluation chain and verify all rows persist."""
    home = _make_team(session, code="LGF", name="LG Twins F")
    away = _make_team(session, code="SSF", name="Samsung F")
    game = Game(
        external_id="G_CHAIN",
        home_team_id=home.id,
        away_team_id=away.id,
        game_date=date(2026, 5, 25),
    )
    session.add(game)
    session.flush()
    player = _make_player(session, home.id, ext="P_CHAIN")
    ir = _make_ingestion_run(session)
    ss = _make_stat_snapshot(session, ir.id)
    ls = _make_lineup_snapshot(session, game.id, ir.id, home.id)
    mv = _make_model_version(session, ver="3.0.0")

    stat_row = PlayerStatSnapshotRow(
        snapshot_id=ss.id, player_id=player.id, stats_json={"avg": 0.312}
    )
    session.add(stat_row)

    lineup_row = ActualLineupSnapshotRow(
        snapshot_id=ls.id, player_id=player.id, batting_order=1, position="CF"
    )
    session.add(lineup_row)

    eval_run = _make_eval_run(session, game.id, home.id, ss.id, ls.id, mv.id)

    rec_row = RecommendedLineupRow(
        evaluation_run_id=eval_run.id,
        player_id=player.id,
        batting_order=1,
        position="CF",
        score=0.95,
    )
    session.add(rec_row)

    summary = LineupEvaluationSummary(
        evaluation_run_id=eval_run.id,
        summary_text="김민준은 오늘 선발 출전이 유력합니다.",
    )
    session.add(summary)

    box_snap = BoxScoreSnapshot(
        game_id=game.id,
        ingestion_run_id=ir.id,
        taken_at=_now(),
        content_hash="c" * 64,
    )
    session.add(box_snap)
    session.flush()

    box_row = BoxScoreRow(
        snapshot_id=box_snap.id,
        player_id=player.id,
        at_bats=4,
        hits=2,
        runs=1,
        rbis=1,
    )
    session.add(box_row)

    pg_run = PostgameReviewRun(
        evaluation_run_id=eval_run.id,
        box_score_snapshot_id=box_snap.id,
        model_version_id=mv.id,
        status="completed",
    )
    session.add(pg_run)
    session.flush()

    pg_summary = PostgameReviewSummary(
        review_run_id=pg_run.id,
        summary_text="추천 라인업 정확도 75%",
        accuracy_score=0.75,
    )
    session.add(pg_summary)
    session.commit()

    assert session.query(RecommendedLineupRow).filter_by(evaluation_run_id=eval_run.id).count() == 1
    assert (
        session.query(LineupEvaluationSummary).filter_by(evaluation_run_id=eval_run.id).count() == 1
    )
    assert session.query(PostgameReviewSummary).filter_by(review_run_id=pg_run.id).count() == 1
