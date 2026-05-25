"""add postgame review uniqueness constraint

Revision ID: d2e3f4a5b6c7
Revises: b1c2d3e4f5a6
Create Date: 2026-05-25 00:00:00.000000

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d2e3f4a5b6c7"  # pragma: allowlist secret
down_revision: str | Sequence[str] | None = "b1c2d3e4f5a6"  # pragma: allowlist secret
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add UNIQUE constraint on (evaluation_run_id, box_score_snapshot_id)."""
    with op.batch_alter_table("postgame_review_runs", schema=None) as batch_op:
        batch_op.create_unique_constraint(
            "uq_postgame_review_runs_eval_box",
            ["evaluation_run_id", "box_score_snapshot_id"],
        )


def downgrade() -> None:
    """Drop the UNIQUE constraint added on postgame_review_runs."""
    with op.batch_alter_table("postgame_review_runs", schema=None) as batch_op:
        batch_op.drop_constraint("uq_postgame_review_runs_eval_box", type_="unique")
