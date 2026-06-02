"""Tests for lineup-history enrichment of eligible hitters."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

import app.models  # noqa: F401 — registers all ORM models with Base.metadata
from app.db.base import Base
from app.lineup_model.types import Handedness, HitterStats, Position
from app.models.evaluation import LineupEvaluationRun
from app.models.snapshot import (
    ActualLineupSnapshot,
    ActualLineupSnapshotRow,
    IngestionRun,
    PlayerStatSnapshotRow,
)
from app.services.lineup_evaluator import (
    _LINEUP_HISTORY_WINDOW,
    _enrich_with_lineup_history,
    _load_recent_lineups,
    compute_actual_lineup_score,
)

_TEAM_ID = 1


@pytest.fixture
def session() -> Iterator[Session]:
    """In-memory SQLite session with the full ORM schema."""
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine)() as s:
        yield s
    engine.dispose()


def _seed_snapshot(
    session: Session,
    *,
    game_id: int,
    announced_at: datetime,
    rows: dict[int, str],
    ingestion_run_id: int,
) -> None:
    """Persist one ActualLineupSnapshot for ``_TEAM_ID`` plus its rows."""
    snap = ActualLineupSnapshot(
        game_id=game_id,
        team_id=_TEAM_ID,
        ingestion_run_id=ingestion_run_id,
        announced_at=announced_at,
        content_hash=f"hash-{game_id}-{announced_at.isoformat()}",
    )
    session.add(snap)
    session.flush()
    for order, (player_id, position) in enumerate(rows.items(), start=1):
        session.add(
            ActualLineupSnapshotRow(
                snapshot_id=snap.id,
                player_id=player_id,
                batting_order=order,
                position=position,
            )
        )
    session.flush()


def _stats(player_id: int, primary: Position) -> HitterStats:
    return HitterStats(
        player_id=player_id,
        handedness=Handedness.RIGHT,
        ops=0.800,
        obp=0.350,
        slg=0.450,
        primary_position=primary,
    )


def test_enrich_adds_recent_positions_and_start_count() -> None:
    """A player who appeared in 6 recent games at three positions gets the
    non-primary ones, plus a starts_last_5 capped by the 5-game window."""
    eligible = [_stats(1, Position.CENTER)]
    # most-recent-first lineups, one dict per game: {player_id: position}
    lineups = [
        {1: "CF"},  # game -1
        {1: "LF"},  # game -2
        {1: "CF"},  # game -3
        {1: "RF"},  # game -4
        {1: "CF"},  # game -5
        {1: "LF"},  # game -6 (outside last-5 for starts, still counts for positions)
    ]
    enriched = _enrich_with_lineup_history(eligible, lineups)
    s = enriched[0]
    assert s.starts_last_5_games == 5
    assert set(s.recent_positions) == {Position.LEFT, Position.RIGHT}


def test_enrich_no_history_is_a_noop() -> None:
    eligible = [_stats(7, Position.SHORT)]
    enriched = _enrich_with_lineup_history(eligible, [])
    assert enriched[0].recent_positions == ()
    assert enriched[0].starts_last_5_games == 0


def test_load_recent_lineups_dedupes_and_respects_cutoff(session: Session) -> None:
    """_load_recent_lineups dedupes two snapshots of the same game to the latest
    announced_at, and excludes snapshots at or after the cutoff."""
    run = IngestionRun(source="lineup", status="completed")
    session.add(run)
    session.flush()

    base = datetime(2026, 5, 1, 12, 0, tzinfo=UTC)
    cutoff = datetime(2026, 5, 10, 12, 0, tzinfo=UTC)

    # Game 100: two snapshots (tentative → final); final must win.
    _seed_snapshot(
        session,
        game_id=100,
        announced_at=base,
        rows={1: "LF"},  # tentative
        ingestion_run_id=run.id,
    )
    _seed_snapshot(
        session,
        game_id=100,
        announced_at=base + timedelta(hours=2),
        rows={1: "CF"},  # final, later announced
        ingestion_run_id=run.id,
    )
    # Game 101: a distinct earlier game.
    _seed_snapshot(
        session,
        game_id=101,
        announced_at=base - timedelta(days=1),
        rows={2: "SS"},
        ingestion_run_id=run.id,
    )
    # Game 999: announced at/after cutoff — must be excluded.
    _seed_snapshot(
        session,
        game_id=999,
        announced_at=cutoff,
        rows={3: "RF"},
        ingestion_run_id=run.id,
    )

    lineups = _load_recent_lineups(session, _TEAM_ID, cutoff)

    # Two distinct games survive (game 100 deduped, game 999 excluded by cutoff).
    assert len(lineups) == 2
    # Most-recent first: game 100's FINAL snapshot, then game 101.
    assert lineups[0] == {1: "CF"}
    assert lineups[1] == {2: "SS"}


def test_load_recent_lineups_caps_to_window(session: Session) -> None:
    """_load_recent_lineups returns at most _LINEUP_HISTORY_WINDOW games even
    when more historical snapshots exist."""
    run = IngestionRun(source="lineup", status="completed")
    session.add(run)
    session.flush()

    cutoff = datetime(2026, 6, 1, 12, 0, tzinfo=UTC)
    total_games = _LINEUP_HISTORY_WINDOW + 2
    for i in range(total_games):
        _seed_snapshot(
            session,
            game_id=200 + i,
            announced_at=cutoff - timedelta(days=i + 1),
            rows={1: "CF"},
            ingestion_run_id=run.id,
        )

    lineups = _load_recent_lineups(session, _TEAM_ID, cutoff)

    assert len(lineups) == _LINEUP_HISTORY_WINDOW


def test_load_recent_lineups_excludes_current_game(session: Session) -> None:
    """When exclude_game_id is passed, the current game's lineup is kept out of
    its own history even though it is announced before the cutoff."""
    run = IngestionRun(source="lineup", status="completed")
    session.add(run)
    session.flush()

    cutoff = datetime(2026, 6, 10, 18, 0, tzinfo=UTC)
    current_game_id = 500

    # Current game: announced BEFORE the cutoff (the common real-world case).
    _seed_snapshot(
        session,
        game_id=current_game_id,
        announced_at=cutoff - timedelta(hours=1),
        rows={1: "CF", 2: "SS"},
        ingestion_run_id=run.id,
    )
    # A genuinely prior game with a different player set.
    _seed_snapshot(
        session,
        game_id=499,
        announced_at=cutoff - timedelta(days=1),
        rows={3: "LF"},
        ingestion_run_id=run.id,
    )

    # Without exclusion the current game leaks in.
    leaky = _load_recent_lineups(session, _TEAM_ID, cutoff)
    assert {1: "CF", 2: "SS"} in leaky

    # With exclusion only the prior game survives.
    filtered = _load_recent_lineups(session, _TEAM_ID, cutoff, exclude_game_id=current_game_id)
    assert filtered == [{3: "LF"}]
    # The current game's players must not pick up any starts from their own game.
    enriched = _enrich_with_lineup_history(
        [_stats(1, Position.CENTER), _stats(2, Position.SHORT)], filtered
    )
    assert enriched[0].starts_last_5_games == 0
    assert enriched[1].starts_last_5_games == 0
    assert enriched[0].recent_positions == ()
    assert enriched[1].recent_positions == ()


def test_load_recent_lineups_tie_break_is_deterministic(session: Session) -> None:
    """Two games sharing an identical announced_at are ordered by game_id desc."""
    run = IngestionRun(source="lineup", status="completed")
    session.add(run)
    session.flush()

    cutoff = datetime(2026, 7, 1, 12, 0, tzinfo=UTC)
    same_announced = cutoff - timedelta(days=1)

    # Seed lower game_id first to prove ordering is by game_id, not insert order.
    _seed_snapshot(
        session,
        game_id=600,
        announced_at=same_announced,
        rows={1: "LF"},
        ingestion_run_id=run.id,
    )
    _seed_snapshot(
        session,
        game_id=601,
        announced_at=same_announced,
        rows={2: "RF"},
        ingestion_run_id=run.id,
    )

    lineups = _load_recent_lineups(session, _TEAM_ID, cutoff)

    # Higher game_id wins the tie-break and comes first.
    assert lineups == [{2: "RF"}, {1: "LF"}]


def _seed_actual_run(
    session: Session,
    *,
    ingest_id: int,
    game_id: int,
    stat_snapshot_id: int,
    cutoff: datetime,
    obp: float,
    slg: float,
) -> LineupEvaluationRun:
    """Seed a one-player actual lineup + stat row and return its pending run."""
    actual_snap = ActualLineupSnapshot(
        game_id=game_id,
        team_id=_TEAM_ID,
        ingestion_run_id=ingest_id,
        announced_at=cutoff - timedelta(hours=1),
        content_hash=f"actual-{game_id}",
    )
    session.add(actual_snap)
    session.flush()
    session.add(
        ActualLineupSnapshotRow(
            snapshot_id=actual_snap.id, player_id=1, batting_order=1, position="CF"
        )
    )
    session.add(
        PlayerStatSnapshotRow(
            snapshot_id=stat_snapshot_id,
            player_id=1,
            stats_json={
                "OPS": round(obp + slg, 3),
                "OBP": obp,
                "SLG": slg,
                "primary_position": "CF",
                "handedness": "R",
            },
        )
    )
    session.flush()
    run = LineupEvaluationRun(
        game_id=game_id,
        team_id=_TEAM_ID,
        model_version_id=1,
        stat_snapshot_id=stat_snapshot_id,
        lineup_snapshot_id=actual_snap.id,
        evaluation_cutoff_at=cutoff,
        status="pending",
    )
    session.add(run)
    session.flush()
    return run


def test_compute_actual_lineup_score_reflects_obp_slg(session: Session) -> None:
    """compute_actual_lineup_score is on the run-expectancy scale and tracks
    offensive quality: a higher-OBP/SLG actual lineup yields strictly more
    expected runs than a lower-OBP/SLG one.

    Run-expectancy change: the lineup score now reflects only season OBP/SLG
    (per-PA event rates → Markov expected runs); start_rhythm
    (``starts_last_5_games``) drives player SELECTION via compute_player_score,
    no longer the lineup score, so it is intentionally not asserted here.
    """
    ingest = IngestionRun(source="lineup", status="completed")
    session.add(ingest)
    session.flush()

    cutoff = datetime(2026, 8, 1, 18, 0, tzinfo=UTC)

    low_run = _seed_actual_run(
        session,
        ingest_id=ingest.id,
        game_id=700,
        stat_snapshot_id=9001,
        cutoff=cutoff,
        obp=0.300,
        slg=0.380,
    )
    high_run = _seed_actual_run(
        session,
        ingest_id=ingest.id,
        game_id=701,
        stat_snapshot_id=9002,
        cutoff=cutoff,
        obp=0.420,
        slg=0.560,
    )

    low_score = compute_actual_lineup_score(session, low_run, Handedness.RIGHT)
    high_score = compute_actual_lineup_score(session, high_run, Handedness.RIGHT)

    # Expected runs live on the ~3-6 scale (single-batter lineup here), and the
    # higher-OBP/SLG lineup scores strictly more.
    assert high_score > low_score
    assert low_score > 0.0


def test_persist_start_rhythm_writes_into_stats_json() -> None:
    """The derived starts_last_5_games is written into the stat row's stats_json
    (existing keys preserved) so read-time views can surface it."""
    from app.services.lineup_evaluator import _persist_start_rhythm

    row = PlayerStatSnapshotRow(snapshot_id=1, player_id=1, stats_json={"OPS": 0.8})
    enriched = [
        HitterStats(
            player_id=1,
            handedness=Handedness.RIGHT,
            ops=0.8,
            obp=0.35,
            slg=0.45,
            primary_position=Position.DH,
            starts_last_5_games=3,
        )
    ]
    _persist_start_rhythm({1: row}, enriched)
    assert row.stats_json["starts_last_5_games"] == 3
    assert row.stats_json["OPS"] == 0.8


def test_persist_start_rhythm_skips_players_without_a_row() -> None:
    """A hitter with no matching stat row is skipped without error."""
    from app.services.lineup_evaluator import _persist_start_rhythm

    row = PlayerStatSnapshotRow(snapshot_id=1, player_id=1, stats_json={"OPS": 0.8})
    enriched = [
        HitterStats(
            player_id=99,
            handedness=Handedness.RIGHT,
            ops=0.8,
            obp=0.35,
            slg=0.45,
            primary_position=Position.DH,
            starts_last_5_games=2,
        )
    ]
    _persist_start_rhythm({1: row}, enriched)
    assert "starts_last_5_games" not in row.stats_json
