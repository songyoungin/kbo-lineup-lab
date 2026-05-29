"""add player handedness columns

Revision ID: a1b2c3d4e5f6
Revises: e3f4a5b6c7d8
Create Date: 2026-05-30 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a1b2c3d4e5f6"  # pragma: allowlist secret
down_revision: str | Sequence[str] | None = "e3f4a5b6c7d8"  # pragma: allowlist secret
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add nullable bats/throws handedness columns to players."""
    op.add_column("players", sa.Column("bats", sa.String(length=8), nullable=True))
    op.add_column("players", sa.Column("throws", sa.String(length=8), nullable=True))


def downgrade() -> None:
    """Drop bats/throws handedness columns from players."""
    op.drop_column("players", "throws")
    op.drop_column("players", "bats")
