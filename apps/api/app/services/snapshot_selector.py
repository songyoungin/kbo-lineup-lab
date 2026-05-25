"""Cutoff-safe snapshot selection helpers.

All cutoff_at arguments must be tz-aware datetimes; naive datetimes raise
ValueError. Internally we normalize the cutoff to UTC before pushing it to
the database to keep comparisons correct against SQLite-style text-storage
backends that compare timestamps lexicographically.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.player import Player
from app.models.snapshot import ActualLineupSnapshot, PlayerStatSnapshotRow, StatSnapshot
from app.util.time import to_utc


class SnapshotNotFoundError(LookupError):
    """No cutoff-safe snapshot exists for the requested key."""

    def __init__(self, snapshot_kind: str, **filters: object) -> None:
        self.snapshot_kind = snapshot_kind
        self.filters = filters
        super().__init__(f"No {snapshot_kind} snapshot at-or-before cutoff for filters={filters}")


def select_stat_snapshot(
    session: Session,
    *,
    team_id: int,
    cutoff_at: datetime,
) -> StatSnapshot:
    """Return the latest StatSnapshot at-or-before cutoff_at with data for the team.

    Resolves team membership via the player_stat_snapshot_rows → players JOIN,
    because stat_snapshots does not carry team_id directly.

    Args:
        session: SQLAlchemy session.
        team_id: Team whose players must appear in the snapshot.
        cutoff_at: Tz-aware upper bound (inclusive). Naive datetimes raise ValueError.

    Returns:
        The most recent StatSnapshot at-or-before cutoff_at for the team.

    Raises:
        ValueError: If cutoff_at is naive.
        SnapshotNotFoundError: If no cutoff-safe snapshot covers the team.
    """
    # to_utc raises ValueError for naive datetimes; normalization is required
    # so that text-based DB comparisons (e.g. SQLite) work for any input tz.
    cutoff_utc = to_utc(cutoff_at)

    stmt = (
        select(StatSnapshot)
        .join(PlayerStatSnapshotRow, PlayerStatSnapshotRow.snapshot_id == StatSnapshot.id)
        .join(Player, Player.id == PlayerStatSnapshotRow.player_id)
        .where(Player.team_id == team_id, StatSnapshot.snapshot_at <= cutoff_utc)
        .order_by(StatSnapshot.snapshot_at.desc())
        # LIMIT 1 after DESC order gives the latest unique snapshot
        .limit(1)
    )
    snapshot = session.execute(stmt).scalars().first()
    if snapshot is None:
        raise SnapshotNotFoundError("stat", team_id=team_id, cutoff_at=cutoff_utc)
    return snapshot


def select_lineup_snapshot(
    session: Session,
    *,
    game_id: int,
    team_id: int,
    cutoff_at: datetime,
) -> ActualLineupSnapshot:
    """Return the latest ActualLineupSnapshot at-or-before cutoff_at for the game/team.

    Args:
        session: SQLAlchemy session.
        game_id: Game the lineup belongs to.
        team_id: Team whose lineup is requested.
        cutoff_at: Tz-aware upper bound (inclusive). Naive datetimes raise ValueError.

    Returns:
        The most recent ActualLineupSnapshot at-or-before cutoff_at.

    Raises:
        ValueError: If cutoff_at is naive.
        SnapshotNotFoundError: If no cutoff-safe lineup snapshot exists.
    """
    cutoff_utc = to_utc(cutoff_at)

    stmt = (
        select(ActualLineupSnapshot)
        .where(
            ActualLineupSnapshot.game_id == game_id,
            ActualLineupSnapshot.team_id == team_id,
            ActualLineupSnapshot.announced_at <= cutoff_utc,
        )
        .order_by(ActualLineupSnapshot.announced_at.desc())
        .limit(1)
    )
    snapshot = session.execute(stmt).scalars().first()
    if snapshot is None:
        raise SnapshotNotFoundError(
            "lineup",
            game_id=game_id,
            team_id=team_id,
            cutoff_at=cutoff_utc,
        )
    return snapshot
