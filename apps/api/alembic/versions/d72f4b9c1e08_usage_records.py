"""P1-8: organization-scoped usage records

Revision ID: d72f4b9c1e08
Revises: c4e18f7b2a55
Create Date: 2026-08-10

One row per metered event rather than a running total, so a billing dispute can
be investigated. The unique index on `idempotency_key` is the guarantee that a
retried job does not bill twice.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "d72f4b9c1e08"
down_revision: Union[str, None] = "c4e18f7b2a55"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "usage_records",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "organization_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "client_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("clients.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("metric", sa.String(length=64), nullable=False),
        sa.Column("quantity", sa.Numeric(18, 4), nullable=False, server_default="0"),
        sa.Column("period", sa.String(length=7), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("idempotency_key", sa.String(length=255), nullable=False),
        sa.Column("details", sa.JSON(), nullable=True),
        sa.Column("unit_count", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index(
        "ix_usage_records_idempotency_key", "usage_records", ["idempotency_key"], unique=True
    )
    op.create_index("ix_usage_records_organization_id", "usage_records", ["organization_id"])
    op.create_index("ix_usage_records_metric", "usage_records", ["metric"])
    op.create_index("ix_usage_records_period", "usage_records", ["period"])
    # Serves both the quota check and the invoice query.
    op.create_index(
        "ix_usage_records_org_period_metric",
        "usage_records",
        ["organization_id", "period", "metric"],
    )


def downgrade() -> None:
    op.drop_index("ix_usage_records_org_period_metric", table_name="usage_records")
    op.drop_index("ix_usage_records_period", table_name="usage_records")
    op.drop_index("ix_usage_records_metric", table_name="usage_records")
    op.drop_index("ix_usage_records_organization_id", table_name="usage_records")
    op.drop_index("ix_usage_records_idempotency_key", table_name="usage_records")
    op.drop_table("usage_records")
