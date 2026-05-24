"""ORM model for KBO games."""

from datetime import UTC, date, datetime

from sqlalchemy import Date, DateTime, ForeignKey, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class Game(Base):
    """Represents a scheduled or completed KBO game."""

    __tablename__ = "games"

    __table_args__ = (
        Index("ix_games_game_date", "game_date"),
        Index("ix_games_home_team_id", "home_team_id"),
        Index("ix_games_away_team_id", "away_team_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    external_id: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)
    home_team_id: Mapped[int] = mapped_column(Integer, ForeignKey("teams.id"), nullable=False)
    away_team_id: Mapped[int] = mapped_column(Integer, ForeignKey("teams.id"), nullable=False)
    game_date: Mapped[date] = mapped_column(Date, nullable=False)
    # Stadium where the game is played
    venue: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        nullable=False,
    )
