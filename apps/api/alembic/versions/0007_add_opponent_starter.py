"""add opponent starter columns to games

Revision ID: c3d4e5f6a7b8
Revises: b2c3d4e5f6a7
Create Date: 2026-06-01 01:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c3d4e5f6a7b8"  # pragma: allowlist secret
down_revision: str | Sequence[str] | None = "b2c3d4e5f6a7"  # pragma: allowlist secret
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add nullable opponent starting-pitcher name/handedness columns to games."""
    op.add_column("games", sa.Column("opponent_starter_name", sa.String(length=64), nullable=True))
    op.add_column("games", sa.Column("opponent_starter_throws", sa.String(length=1), nullable=True))


def downgrade() -> None:
    """Drop the opponent starting-pitcher columns."""
    op.drop_column("games", "opponent_starter_throws")
    op.drop_column("games", "opponent_starter_name")
