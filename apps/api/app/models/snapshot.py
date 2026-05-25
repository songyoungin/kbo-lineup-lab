"""ORM models for ingestion runs and data snapshots.

Tables defined here:
- ingestion_runs
- stat_snapshots
- player_stat_snapshot_rows
- actual_lineup_snapshots
- actual_lineup_snapshot_rows
- box_score_snapshots
- box_score_rows
"""

from datetime import UTC, datetime

from sqlalchemy import (
    JSON,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class IngestionRun(Base):
    """Tracks a single external-data ingestion job."""

    __tablename__ = "ingestion_runs"

    __table_args__ = (
        UniqueConstraint("source", name="uq_ingestion_runs_source"),
        Index("ix_ingestion_runs_status", "status"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    source: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        nullable=False,
    )


class StatSnapshot(Base):
    """Immutable snapshot of player batting/pitching stats at a point in time."""

    __tablename__ = "stat_snapshots"

    __table_args__ = (
        UniqueConstraint("content_hash", name="uq_stat_snapshots_content_hash"),
        Index("ix_stat_snapshots_snapshot_at", "snapshot_at"),
        Index("ix_stat_snapshots_ingestion_run_id", "ingestion_run_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    ingestion_run_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("ingestion_runs.id"), nullable=False
    )
    snapshot_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    # Hash of the canonical JSON payload used as a content fingerprint
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        nullable=False,
    )


class PlayerStatSnapshotRow(Base):
    """One player's stat values inside a stat snapshot."""

    __tablename__ = "player_stat_snapshot_rows"

    __table_args__ = (
        Index("ix_player_stat_snapshot_rows_snapshot_id", "snapshot_id"),
        Index("ix_player_stat_snapshot_rows_player_id", "player_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    snapshot_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("stat_snapshots.id"), nullable=False
    )
    player_id: Mapped[int] = mapped_column(Integer, ForeignKey("players.id"), nullable=False)
    # Flexible JSON blob so we can add new stat fields without migrations
    stats_json: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)


class ActualLineupSnapshot(Base):
    """Immutable snapshot of the officially announced game lineup.

    Each row pins the lineup to exactly one (game, team). Multiple snapshots
    over time are allowed (e.g. tentative → final), but each (game, team)
    pair must have a distinct announced_at timestamp.
    """

    __tablename__ = "actual_lineup_snapshots"

    __table_args__ = (
        UniqueConstraint(
            "game_id",
            "team_id",
            "announced_at",
            name="uq_actual_lineup_snapshots_game_team_announced",
        ),
        Index("ix_actual_lineup_snapshots_announced_at", "announced_at"),
        Index("ix_actual_lineup_snapshots_game_id", "game_id"),
        Index("ix_actual_lineup_snapshots_team_id", "team_id"),
        Index("ix_actual_lineup_snapshots_ingestion_run_id", "ingestion_run_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    game_id: Mapped[int] = mapped_column(Integer, ForeignKey("games.id"), nullable=False)
    team_id: Mapped[int] = mapped_column(Integer, ForeignKey("teams.id"), nullable=False)
    ingestion_run_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("ingestion_runs.id"), nullable=False
    )
    announced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        nullable=False,
    )


class ActualLineupSnapshotRow(Base):
    """One player slot in an actual lineup snapshot."""

    __tablename__ = "actual_lineup_snapshot_rows"

    __table_args__ = (
        Index("ix_actual_lineup_snapshot_rows_snapshot_id", "snapshot_id"),
        Index("ix_actual_lineup_snapshot_rows_player_id", "player_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    snapshot_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("actual_lineup_snapshots.id"), nullable=False
    )
    player_id: Mapped[int] = mapped_column(Integer, ForeignKey("players.id"), nullable=False)
    batting_order: Mapped[int | None] = mapped_column(Integer, nullable=True)
    position: Mapped[str] = mapped_column(String(8), nullable=False)


class BoxScoreSnapshot(Base):
    """Immutable snapshot of a completed game's box score."""

    __tablename__ = "box_score_snapshots"

    __table_args__ = (
        UniqueConstraint("content_hash", name="uq_box_score_snapshots_content_hash"),
        Index("ix_box_score_snapshots_taken_at", "taken_at"),
        Index("ix_box_score_snapshots_game_id", "game_id"),
        Index("ix_box_score_snapshots_ingestion_run_id", "ingestion_run_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    game_id: Mapped[int] = mapped_column(Integer, ForeignKey("games.id"), nullable=False)
    ingestion_run_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("ingestion_runs.id"), nullable=False
    )
    taken_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        nullable=False,
    )


class BoxScoreRow(Base):
    """One player's box score line within a box score snapshot."""

    __tablename__ = "box_score_rows"

    __table_args__ = (
        Index("ix_box_score_rows_snapshot_id", "snapshot_id"),
        Index("ix_box_score_rows_player_id", "player_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    snapshot_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("box_score_snapshots.id"), nullable=False
    )
    player_id: Mapped[int] = mapped_column(Integer, ForeignKey("players.id"), nullable=False)
    at_bats: Mapped[int | None] = mapped_column(Integer, nullable=True)
    hits: Mapped[int | None] = mapped_column(Integer, nullable=True)
    runs: Mapped[int | None] = mapped_column(Integer, nullable=True)
    rbis: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Flexible JSON blob for additional stat fields (ERA, WHIP, K, etc.)
    extra_stats_json: Mapped[dict[str, object] | None] = mapped_column(JSON, nullable=True)
    # Pitcher-specific: innings pitched stored as float (e.g. 6.2 = 6⅔)
    innings_pitched: Mapped[float | None] = mapped_column(Float, nullable=True)
