"""ORM models for model versions and lineup evaluation runs.

Tables defined here:
- model_versions
- lineup_evaluation_runs
- recommended_lineup_rows
- lineup_evaluation_summaries
"""

from datetime import UTC, datetime

from sqlalchemy import JSON, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class ModelVersion(Base):
    """Tracks a versioned model used for lineup evaluation."""

    __tablename__ = "model_versions"

    __table_args__ = (UniqueConstraint("name", "version", name="uq_model_versions_name_version"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    version: Mapped[str] = mapped_column(String(64), nullable=False)
    # Provider + model identifier (e.g. "anthropic/claude-opus-4")
    model_id: Mapped[str] = mapped_column(String(256), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        nullable=False,
    )


class LineupEvaluationRun(Base):
    """One complete pregame lineup evaluation produced by the model.

    The six-column UNIQUE constraint implements idempotency so that
    re-runs with identical inputs produce the same database row rather
    than duplicate rows.
    """

    __tablename__ = "lineup_evaluation_runs"

    __table_args__ = (
        UniqueConstraint(
            "game_id",
            "team_id",
            "evaluation_cutoff_at",
            "stat_snapshot_id",
            "lineup_snapshot_id",
            "model_version_id",
            name="uq_lineup_evaluation_runs_idempotency",
        ),
        Index("ix_lineup_evaluation_runs_status", "status"),
        Index("ix_lineup_evaluation_runs_game_id", "game_id"),
        Index("ix_lineup_evaluation_runs_team_id", "team_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    game_id: Mapped[int] = mapped_column(Integer, ForeignKey("games.id"), nullable=False)
    team_id: Mapped[int] = mapped_column(Integer, ForeignKey("teams.id"), nullable=False)
    model_version_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("model_versions.id"), nullable=False
    )
    stat_snapshot_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("stat_snapshots.id"), nullable=False
    )
    lineup_snapshot_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("actual_lineup_snapshots.id"), nullable=False
    )
    # The latest timestamp of any input data included in this evaluation
    evaluation_cutoff_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    # Canonical JSON of all inputs; used to derive input_hash
    input_manifest_json: Mapped[dict[str, object] | None] = mapped_column(JSON, nullable=True)
    input_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    output_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    model_config_json: Mapped[dict[str, object] | None] = mapped_column(JSON, nullable=True)
    # Git SHA or container digest of the code that produced this run
    code_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        nullable=False,
    )


class RecommendedLineupRow(Base):
    """One player slot in the model-recommended lineup for an evaluation run."""

    __tablename__ = "recommended_lineup_rows"

    __table_args__ = (
        Index("ix_recommended_lineup_rows_run_id", "evaluation_run_id"),
        Index("ix_recommended_lineup_rows_player_id", "player_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    evaluation_run_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("lineup_evaluation_runs.id"), nullable=False
    )
    player_id: Mapped[int] = mapped_column(Integer, ForeignKey("players.id"), nullable=False)
    batting_order: Mapped[int | None] = mapped_column(Integer, nullable=True)
    position: Mapped[str] = mapped_column(String(8), nullable=False)
    # Model-assigned score justifying the selection
    score: Mapped[float | None] = mapped_column(nullable=True)
    rationale: Mapped[str | None] = mapped_column(Text, nullable=True)


class LineupEvaluationSummary(Base):
    """Narrative summary produced by the model for an evaluation run."""

    __tablename__ = "lineup_evaluation_summaries"

    __table_args__ = (Index("ix_lineup_evaluation_summaries_run_id", "evaluation_run_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    evaluation_run_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("lineup_evaluation_runs.id"), nullable=False, unique=True
    )
    summary_text: Mapped[str] = mapped_column(Text, nullable=False)
    key_insights_json: Mapped[dict[str, object] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        nullable=False,
    )
