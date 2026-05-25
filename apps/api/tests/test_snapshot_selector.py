"""Tests for cutoff-safe snapshot selection.

Verifies:
- Latest snapshot at-or-before cutoff is returned
- Future snapshots (beyond cutoff) are ignored
- Missing snapshots raise SnapshotNotFoundError
- Team filter is enforced via player join (stat snapshots)
- Naive cutoff_at raises ValueError
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

import app.models  # noqa: F401 — registers all models with Base.metadata
from app.db.base import Base
from app.models.game import Game
from app.models.player import Player
from app.models.snapshot import (
    ActualLineupSnapshot,
    IngestionRun,
    PlayerStatSnapshotRow,
    StatSnapshot,
)
from app.models.team import Team
from app.services.snapshot_selector import (
    SnapshotNotFoundError,
    select_lineup_snapshot,
    select_stat_snapshot,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def session() -> Iterator[Session]:
    """In-memory SQLite session with full schema."""
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine)() as s:
        yield s
    engine.dispose()


def _make_team(session: Session, code: str) -> Team:
    team = Team(code=code, name=code)
    session.add(team)
    session.flush()
    return team


def _make_player(session: Session, team_id: int, ext: str) -> Player:
    player = Player(team_id=team_id, external_id=ext, name=ext, position="P")
    session.add(player)
    session.flush()
    return player


def _make_ingestion_run(session: Session, source: str) -> IngestionRun:
    run = IngestionRun(source=source, status="completed")
    session.add(run)
    session.flush()
    return run


def _make_stat_snapshot(
    session: Session, ingestion_run_id: int, snapshot_at: datetime, content_hash: str
) -> StatSnapshot:
    snap = StatSnapshot(
        ingestion_run_id=ingestion_run_id,
        snapshot_at=snapshot_at,
        content_hash=content_hash,
    )
    session.add(snap)
    session.flush()
    return snap


def _make_player_stat_row(
    session: Session, snapshot_id: int, player_id: int
) -> PlayerStatSnapshotRow:
    row = PlayerStatSnapshotRow(
        snapshot_id=snapshot_id,
        player_id=player_id,
        stats_json={"avg": 0.300},
    )
    session.add(row)
    session.flush()
    return row


def _make_game(session: Session, home_team_id: int, away_team_id: int, ext: str = "G001") -> Game:
    from datetime import date

    game = Game(
        external_id=ext,
        home_team_id=home_team_id,
        away_team_id=away_team_id,
        game_date=date(2026, 4, 15),
    )
    session.add(game)
    session.flush()
    return game


def _make_lineup_snapshot(
    session: Session,
    game_id: int,
    team_id: int,
    ingestion_run_id: int,
    announced_at: datetime,
    content_hash: str,
) -> ActualLineupSnapshot:
    snap = ActualLineupSnapshot(
        game_id=game_id,
        team_id=team_id,
        ingestion_run_id=ingestion_run_id,
        announced_at=announced_at,
        content_hash=content_hash,
    )
    session.add(snap)
    session.flush()
    return snap


# Reference timestamps (all UTC)
T1 = datetime(2026, 4, 15, 6, 0, 0, tzinfo=UTC)
T2 = datetime(2026, 4, 15, 8, 0, 0, tzinfo=UTC)
T3 = datetime(2026, 4, 15, 10, 0, 0, tzinfo=UTC)
T_FUTURE = datetime(2026, 4, 15, 20, 0, 0, tzinfo=UTC)


# ---------------------------------------------------------------------------
# select_stat_snapshot tests
# ---------------------------------------------------------------------------


def test_select_stat_snapshot_returns_latest_before_cutoff(session: Session) -> None:
    """Three snapshots at T1 < T2 < T3; cutoff=T2 must return snapshot at T2."""
    team = _make_team(session, "LG")
    player = _make_player(session, team.id, "LG-P001")
    run = _make_ingestion_run(session, "src-stat-latest")

    snap1 = _make_stat_snapshot(session, run.id, T1, "hash-s1")
    snap2 = _make_stat_snapshot(session, run.id, T2, "hash-s2")
    snap3 = _make_stat_snapshot(session, run.id, T3, "hash-s3")

    for snap in (snap1, snap2, snap3):
        _make_player_stat_row(session, snap.id, player.id)

    result = select_stat_snapshot(session, team_id=team.id, cutoff_at=T2)
    assert result.id == snap2.id
    assert result.snapshot_at == T2


def test_select_stat_snapshot_ignores_future_snapshots(session: Session) -> None:
    """Snapshot at T_FUTURE > T2 must NOT be returned when cutoff=T2."""
    team = _make_team(session, "KT")
    player = _make_player(session, team.id, "KT-P001")
    run = _make_ingestion_run(session, "src-stat-future")

    snap_past = _make_stat_snapshot(session, run.id, T1, "hash-f1")
    snap_future = _make_stat_snapshot(session, run.id, T_FUTURE, "hash-f2")

    _make_player_stat_row(session, snap_past.id, player.id)
    _make_player_stat_row(session, snap_future.id, player.id)

    result = select_stat_snapshot(session, team_id=team.id, cutoff_at=T2)
    assert result.id == snap_past.id


def test_select_stat_snapshot_raises_when_no_safe_snapshot(session: Session) -> None:
    """Only a future snapshot exists; must raise SnapshotNotFoundError."""
    team = _make_team(session, "SSG")
    player = _make_player(session, team.id, "SSG-P001")
    run = _make_ingestion_run(session, "src-stat-none")

    snap_future = _make_stat_snapshot(session, run.id, T_FUTURE, "hash-n1")
    _make_player_stat_row(session, snap_future.id, player.id)

    with pytest.raises(SnapshotNotFoundError) as exc_info:
        select_stat_snapshot(session, team_id=team.id, cutoff_at=T1)

    error = exc_info.value
    assert error.snapshot_kind == "stat"
    assert error.filters["team_id"] == team.id


def test_select_stat_snapshot_filters_by_team(session: Session) -> None:
    """Snapshot containing only other-team players must not be returned for our team."""
    team_a = _make_team(session, "NC")
    team_b = _make_team(session, "HH")
    player_b = _make_player(session, team_b.id, "HH-P001")
    run = _make_ingestion_run(session, "src-stat-team")

    snap = _make_stat_snapshot(session, run.id, T1, "hash-t1")
    _make_player_stat_row(session, snap.id, player_b.id)  # only team_b data

    # team_a has no rows in any snapshot
    with pytest.raises(SnapshotNotFoundError) as exc_info:
        select_stat_snapshot(session, team_id=team_a.id, cutoff_at=T2)

    assert exc_info.value.snapshot_kind == "stat"


def test_select_stat_snapshot_rejects_naive_cutoff(session: Session) -> None:
    """Naive cutoff_at (no tzinfo) must raise ValueError immediately."""
    team = _make_team(session, "SK")
    naive_cutoff = datetime(2026, 4, 15, 8, 0, 0)  # no tzinfo

    with pytest.raises(ValueError, match="naive datetime"):
        select_stat_snapshot(session, team_id=team.id, cutoff_at=naive_cutoff)


# ---------------------------------------------------------------------------
# select_lineup_snapshot tests
# ---------------------------------------------------------------------------


def test_select_lineup_snapshot_returns_latest_before_cutoff(session: Session) -> None:
    """Three lineup snapshots at T1 < T2 < T3; cutoff=T2 must return snapshot at T2."""
    team = _make_team(session, "LGU")
    opp = _make_team(session, "OPP")
    game = _make_game(session, team.id, opp.id, "G-LS-latest")
    run = _make_ingestion_run(session, "src-ls-latest")

    _make_lineup_snapshot(session, game.id, team.id, run.id, T1, "lhash-1")
    snap2 = _make_lineup_snapshot(session, game.id, team.id, run.id, T2, "lhash-2")
    snap3 = _make_lineup_snapshot(session, game.id, team.id, run.id, T3, "lhash-3")

    result = select_lineup_snapshot(session, game_id=game.id, team_id=team.id, cutoff_at=T2)
    assert result.id == snap2.id
    assert result.announced_at == T2

    # snap3 is beyond cutoff and must not be returned; it still exists in DB
    assert snap3.id is not None


def test_select_lineup_snapshot_ignores_future_snapshots(session: Session) -> None:
    """Lineup snapshot at T_FUTURE must not appear when cutoff=T2."""
    team = _make_team(session, "KIA")
    opp = _make_team(session, "OPP2")
    game = _make_game(session, team.id, opp.id, "G-LS-future")
    run = _make_ingestion_run(session, "src-ls-future")

    snap_past = _make_lineup_snapshot(session, game.id, team.id, run.id, T1, "lf-hash-1")
    snap_future = _make_lineup_snapshot(session, game.id, team.id, run.id, T_FUTURE, "lf-hash-2")

    result = select_lineup_snapshot(session, game_id=game.id, team_id=team.id, cutoff_at=T2)
    assert result.id == snap_past.id
    assert snap_future.id is not None


def test_select_lineup_snapshot_raises_when_no_safe_snapshot(session: Session) -> None:
    """Only a future lineup snapshot; must raise SnapshotNotFoundError."""
    team = _make_team(session, "SAM")
    opp = _make_team(session, "OPP3")
    game = _make_game(session, team.id, opp.id, "G-LS-none")
    run = _make_ingestion_run(session, "src-ls-none")

    _make_lineup_snapshot(session, game.id, team.id, run.id, T_FUTURE, "ln-hash-1")

    with pytest.raises(SnapshotNotFoundError) as exc_info:
        select_lineup_snapshot(session, game_id=game.id, team_id=team.id, cutoff_at=T1)

    error = exc_info.value
    assert error.snapshot_kind == "lineup"
    assert error.filters["game_id"] == game.id
    assert error.filters["team_id"] == team.id


def test_select_lineup_snapshot_filters_by_game_and_team(session: Session) -> None:
    """Lineup for a different team in the same game must not be returned."""
    team_a = _make_team(session, "WIZ")
    team_b = _make_team(session, "OPP4")
    game = _make_game(session, team_a.id, team_b.id, "G-LS-filter")
    run = _make_ingestion_run(session, "src-ls-filter")

    # Only team_b has a lineup snapshot
    _make_lineup_snapshot(session, game.id, team_b.id, run.id, T1, "lgt-hash-1")

    with pytest.raises(SnapshotNotFoundError) as exc_info:
        select_lineup_snapshot(session, game_id=game.id, team_id=team_a.id, cutoff_at=T2)

    assert exc_info.value.snapshot_kind == "lineup"


def test_select_lineup_snapshot_rejects_naive_cutoff(session: Session) -> None:
    """Naive cutoff_at must raise ValueError immediately."""
    team = _make_team(session, "DOO")
    opp = _make_team(session, "OPP5")
    game = _make_game(session, team.id, opp.id, "G-LS-naive")
    naive_cutoff = datetime(2026, 4, 15, 8, 0, 0)  # no tzinfo

    with pytest.raises(ValueError, match="naive datetime"):
        select_lineup_snapshot(session, game_id=game.id, team_id=team.id, cutoff_at=naive_cutoff)


def test_select_stat_snapshot_exact_cutoff_is_included(session: Session) -> None:
    """Snapshot exactly at cutoff_at must be returned (inclusive bound)."""
    team = _make_team(session, "LGE")
    player = _make_player(session, team.id, "LGE-P001")
    run = _make_ingestion_run(session, "src-stat-exact")

    snap = _make_stat_snapshot(session, run.id, T2, "hash-exact-s")
    _make_player_stat_row(session, snap.id, player.id)

    result = select_stat_snapshot(session, team_id=team.id, cutoff_at=T2)
    assert result.id == snap.id


def test_select_lineup_snapshot_exact_cutoff_is_included(session: Session) -> None:
    """Lineup snapshot exactly at cutoff_at must be returned (inclusive bound)."""
    team = _make_team(session, "LGL")
    opp = _make_team(session, "OPP6")
    game = _make_game(session, team.id, opp.id, "G-LS-exact")
    run = _make_ingestion_run(session, "src-ls-exact")

    snap = _make_lineup_snapshot(session, game.id, team.id, run.id, T2, "l-exact-hash")

    result = select_lineup_snapshot(session, game_id=game.id, team_id=team.id, cutoff_at=T2)
    assert result.id == snap.id


def test_select_stat_snapshot_non_utc_tz_aware_cutoff(session: Session) -> None:
    """Non-UTC but tz-aware cutoff_at must work correctly (comparison across tz)."""
    team = _make_team(session, "LGKST")
    player = _make_player(session, team.id, "LGKST-P001")
    run = _make_ingestion_run(session, "src-stat-kst")

    snap = _make_stat_snapshot(session, run.id, T2, "hash-kst-s")
    _make_player_stat_row(session, snap.id, player.id)

    # T2 expressed in KST (+09:00) = T2 + 9 hours in wall-clock but same instant
    kst = timezone(timedelta(hours=9))
    t2_kst = T2.astimezone(kst)

    # Cutoff in KST that equals T2 UTC — must still find the snapshot
    result = select_stat_snapshot(session, team_id=team.id, cutoff_at=t2_kst)
    assert result.id == snap.id


def test_select_stat_snapshot_negative_offset_cutoff_regression(session: Session) -> None:
    """Regression: EST cutoff (UTC-5) must find a snapshot stored at the same UTC instant.

    Without UTC normalization, SQLite compares datetimes as text and the EST
    wall-clock string (e.g. "03:00:00-05:00") sorts BEFORE the UTC stored value
    ("08:00:00+00:00") even though they represent the same instant. This test
    fails on the pre-fix code and passes once the selector normalizes to UTC.
    """
    team = _make_team(session, "EST")
    player = _make_player(session, team.id, "EST-P001")
    run = _make_ingestion_run(session, "src-stat-est")

    # Snapshot stored at 2026-04-15T07:00:00Z (UTC)
    snap_at = datetime(2026, 4, 15, 7, 0, 0, tzinfo=UTC)
    snap = _make_stat_snapshot(session, run.id, snap_at, "hash-est-s")
    _make_player_stat_row(session, snap.id, player.id)

    # Cutoff at 2026-04-15T03:00:00-05:00 — same instant as 08:00:00 UTC
    est = timezone(timedelta(hours=-5))
    cutoff_est = datetime(2026, 4, 15, 3, 0, 0, tzinfo=est)

    result = select_stat_snapshot(session, team_id=team.id, cutoff_at=cutoff_est)
    assert result.id == snap.id


def test_select_lineup_snapshot_negative_offset_cutoff_regression(session: Session) -> None:
    """Regression: EST cutoff (UTC-5) must find a lineup snapshot stored at the same UTC instant."""
    team = _make_team(session, "ESL")
    opp = _make_team(session, "OPP-ESL")
    game = _make_game(session, team.id, opp.id, "G-LS-est")
    run = _make_ingestion_run(session, "src-ls-est")

    # Lineup stored at 2026-04-15T07:00:00Z (UTC)
    announced = datetime(2026, 4, 15, 7, 0, 0, tzinfo=UTC)
    snap = _make_lineup_snapshot(session, game.id, team.id, run.id, announced, "lhash-est")

    # Cutoff at 2026-04-15T03:00:00-05:00 — same instant as 08:00:00 UTC
    est = timezone(timedelta(hours=-5))
    cutoff_est = datetime(2026, 4, 15, 3, 0, 0, tzinfo=est)

    result = select_lineup_snapshot(session, game_id=game.id, team_id=team.id, cutoff_at=cutoff_est)
    assert result.id == snap.id
