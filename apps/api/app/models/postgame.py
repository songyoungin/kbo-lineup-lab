"""ORM models for postgame review runs and summaries.

Tables defined here:
- postgame_review_runs
- postgame_review_summaries
"""

from datetime import UTC, datetime

from sqlalchemy import JSON, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class PostgameReviewRun(Base):
    """One postgame review that compares the pregame recommendation to the actual result."""

    __tablename__ = "postgame_review_runs"

    __table_args__ = (
        UniqueConstraint(
            "evaluation_run_id",
            "box_score_snapshot_id",
            name="uq_postgame_review_runs_eval_box",
        ),
        Index("ix_postgame_review_runs_status", "status"),
        Index("ix_postgame_review_runs_evaluation_run_id", "evaluation_run_id"),
        Index("ix_postgame_review_runs_box_score_snapshot_id", "box_score_snapshot_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    # The pregame evaluation this review is based on
    evaluation_run_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("lineup_evaluation_runs.id"), nullable=False
    )
    box_score_snapshot_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("box_score_snapshots.id"), nullable=False
    )
    model_version_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("model_versions.id"), nullable=False
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    input_manifest_json: Mapped[dict[str, object] | None] = mapped_column(JSON, nullable=True)
    input_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    output_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    model_config_json: Mapped[dict[str, object] | None] = mapped_column(JSON, nullable=True)
    code_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        nullable=False,
    )


class PostgameReviewSummary(Base):
    """Narrative and structured review output from a postgame review run."""

    __tablename__ = "postgame_review_summaries"

    __table_args__ = (Index("ix_postgame_review_summaries_review_run_id", "review_run_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    review_run_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("postgame_review_runs.id"), nullable=False, unique=True
    )
    summary_text: Mapped[str] = mapped_column(Text, nullable=False)
    # Structured comparison data: predicted vs actual per player
    comparison_json: Mapped[dict[str, object] | None] = mapped_column(JSON, nullable=True)
    # Overall accuracy score if computable (0.0–1.0)
    accuracy_score: Mapped[float | None] = mapped_column(nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        nullable=False,
    )
