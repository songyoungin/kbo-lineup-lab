"""ORM model for KBO teams."""

from datetime import UTC, datetime

from sqlalchemy import DateTime, Index, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class Team(Base):
    """Represents a KBO team (e.g. LG Twins)."""

    __tablename__ = "teams"

    __table_args__ = (Index("ix_teams_code", "code"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(8), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        nullable=False,
    )
