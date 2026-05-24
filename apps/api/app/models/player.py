"""ORM model for KBO players."""

from datetime import UTC, datetime

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class Player(Base):
    """Represents a KBO player belonging to a team."""

    __tablename__ = "players"

    __table_args__ = (
        Index("ix_players_team_id", "team_id"),
        Index("ix_players_external_id", "external_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    team_id: Mapped[int] = mapped_column(Integer, ForeignKey("teams.id"), nullable=False)
    external_id: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    position: Mapped[str] = mapped_column(String(8), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        nullable=False,
    )
