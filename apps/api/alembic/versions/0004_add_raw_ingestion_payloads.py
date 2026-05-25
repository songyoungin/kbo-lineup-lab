"""add raw ingestion payloads table

Revision ID: e3f4a5b6c7d8
Revises: d2e3f4a5b6c7
Create Date: 2026-05-25 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e3f4a5b6c7d8"  # pragma: allowlist secret
down_revision: str | Sequence[str] | None = "d2e3f4a5b6c7"  # pragma: allowlist secret
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create raw_ingestion_payloads table with UNIQUE constraint and indexes."""
    op.create_table(
        "raw_ingestion_payloads",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("ingestion_run_id", sa.Integer(), nullable=False),
        sa.Column("category", sa.String(length=32), nullable=False),
        sa.Column("source_name", sa.String(length=64), nullable=False),
        sa.Column("source_url", sa.String(length=1024), nullable=False),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("content_type", sa.String(length=128), nullable=False),
        sa.Column("payload_hash", sa.String(length=64), nullable=False),
        sa.Column("raw_body", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["ingestion_run_id"],
            ["ingestion_runs.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "source_name",
            "source_url",
            "payload_hash",
            name="uq_raw_ingestion_payloads_source_url_hash",
        ),
    )
    with op.batch_alter_table("raw_ingestion_payloads", schema=None) as batch_op:
        batch_op.create_index("ix_raw_ingestion_payloads_category", ["category"], unique=False)
        batch_op.create_index("ix_raw_ingestion_payloads_fetched_at", ["fetched_at"], unique=False)
        batch_op.create_index(
            "ix_raw_ingestion_payloads_ingestion_run_id",
            ["ingestion_run_id"],
            unique=False,
        )


def downgrade() -> None:
    """Drop raw_ingestion_payloads table and its indexes."""
    with op.batch_alter_table("raw_ingestion_payloads", schema=None) as batch_op:
        batch_op.drop_index("ix_raw_ingestion_payloads_ingestion_run_id")
        batch_op.drop_index("ix_raw_ingestion_payloads_fetched_at")
        batch_op.drop_index("ix_raw_ingestion_payloads_category")

    op.drop_table("raw_ingestion_payloads")
