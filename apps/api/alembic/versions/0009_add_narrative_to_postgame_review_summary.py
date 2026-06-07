"""add narrative to postgame_review_summaries

Revision ID: e5f6a7b8c9d0
Revises: d4e5f6a7b8c9
Create Date: 2026-06-07

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e5f6a7b8c9d0"  # pragma: allowlist secret
down_revision: str | Sequence[str] | None = "d4e5f6a7b8c9"  # pragma: allowlist secret
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add the nullable narrative column to postgame_review_summaries."""
    op.add_column(
        "postgame_review_summaries",
        sa.Column("narrative", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    """Drop the narrative column from postgame_review_summaries."""
    op.drop_column("postgame_review_summaries", "narrative")
