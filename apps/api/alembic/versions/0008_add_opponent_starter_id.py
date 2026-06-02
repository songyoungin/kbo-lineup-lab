"""add opponent_starter_id to games

Revision ID: d4e5f6a7b8c9
Revises: c3d4e5f6a7b8
Create Date: 2026-06-02 01:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d4e5f6a7b8c9"  # pragma: allowlist secret
down_revision: str | Sequence[str] | None = "c3d4e5f6a7b8"  # pragma: allowlist secret
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add the nullable opponent starting-pitcher KBO player code column to games."""
    op.add_column("games", sa.Column("opponent_starter_id", sa.String(length=32), nullable=True))


def downgrade() -> None:
    """Drop the opponent starting-pitcher KBO player code column."""
    op.drop_column("games", "opponent_starter_id")
