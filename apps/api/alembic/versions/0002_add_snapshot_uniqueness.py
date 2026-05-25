"""add snapshot uniqueness constraints

Revision ID: b1c2d3e4f5a6
Revises: c6ff0df5c965
Create Date: 2026-05-25 00:00:00.000000

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b1c2d3e4f5a6"  # pragma: allowlist secret
down_revision: str | Sequence[str] | None = "c6ff0df5c965"  # pragma: allowlist secret
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add UNIQUE constraints on ingestion_runs.source and snapshot content_hash columns."""
    with op.batch_alter_table("ingestion_runs", schema=None) as batch_op:
        batch_op.create_unique_constraint("uq_ingestion_runs_source", ["source"])
    with op.batch_alter_table("stat_snapshots", schema=None) as batch_op:
        batch_op.create_unique_constraint("uq_stat_snapshots_content_hash", ["content_hash"])
    with op.batch_alter_table("box_score_snapshots", schema=None) as batch_op:
        batch_op.create_unique_constraint("uq_box_score_snapshots_content_hash", ["content_hash"])


def downgrade() -> None:
    """Drop the three UNIQUE constraints added in this revision."""
    with op.batch_alter_table("box_score_snapshots", schema=None) as batch_op:
        batch_op.drop_constraint("uq_box_score_snapshots_content_hash", type_="unique")
    with op.batch_alter_table("stat_snapshots", schema=None) as batch_op:
        batch_op.drop_constraint("uq_stat_snapshots_content_hash", type_="unique")
    with op.batch_alter_table("ingestion_runs", schema=None) as batch_op:
        batch_op.drop_constraint("uq_ingestion_runs_source", type_="unique")
