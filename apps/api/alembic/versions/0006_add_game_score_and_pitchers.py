"""add game score and pitcher-decision columns

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
Create Date: 2026-06-01 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b2c3d4e5f6a7"  # pragma: allowlist secret
down_revision: str | Sequence[str] | None = "a1b2c3d4e5f6"  # pragma: allowlist secret
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add nullable final-score, status and pitcher-decision columns to games."""
    op.add_column("games", sa.Column("home_score", sa.Integer(), nullable=True))
    op.add_column("games", sa.Column("away_score", sa.Integer(), nullable=True))
    op.add_column("games", sa.Column("status", sa.String(length=16), nullable=True))
    op.add_column("games", sa.Column("winning_pitcher_name", sa.String(length=64), nullable=True))
    op.add_column("games", sa.Column("losing_pitcher_name", sa.String(length=64), nullable=True))
    op.add_column("games", sa.Column("save_pitcher_name", sa.String(length=64), nullable=True))


def downgrade() -> None:
    """Drop the game score/status/pitcher-decision columns."""
    op.drop_column("games", "save_pitcher_name")
    op.drop_column("games", "losing_pitcher_name")
    op.drop_column("games", "winning_pitcher_name")
    op.drop_column("games", "status")
    op.drop_column("games", "away_score")
    op.drop_column("games", "home_score")
