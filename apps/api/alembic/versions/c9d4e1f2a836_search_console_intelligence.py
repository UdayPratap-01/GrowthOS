"""Search Console intelligence — syncs, performance rows, opportunities.

Revision ID: c9d4e1f2a836
Revises: b7e4a2c9d015
Create Date: 2026-09-27
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "c9d4e1f2a836"
down_revision: Union[str, None] = "b7e4a2c9d015"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "search_console_syncs",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.Column("client_id", sa.UUID(), nullable=True),
        sa.Column("site_url", sa.String(length=2048), nullable=False),
        sa.Column("sync_key", sa.String(length=128), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("preset", sa.String(length=32), nullable=True),
        sa.Column("requested_start_date", sa.Date(), nullable=False),
        sa.Column("requested_end_date", sa.Date(), nullable=False),
        sa.Column("effective_start_date", sa.Date(), nullable=False),
        sa.Column("effective_end_date", sa.Date(), nullable=False),
        sa.Column("compare_start_date", sa.Date(), nullable=True),
        sa.Column("compare_end_date", sa.Date(), nullable=True),
        sa.Column("row_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("opportunity_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("meta", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(["client_id"], ["clients.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("organization_id", "sync_key", name="uq_search_console_syncs_org_key"),
    )
    op.create_index("ix_search_console_syncs_organization_id", "search_console_syncs", ["organization_id"])
    op.create_index("ix_search_console_syncs_status", "search_console_syncs", ["status"])
    op.create_index("ix_search_console_syncs_org_status", "search_console_syncs", ["organization_id", "status"])

    op.create_table(
        "search_console_performance_rows",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("sync_id", sa.UUID(), nullable=False),
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.Column("site_url", sa.String(length=2048), nullable=False),
        sa.Column("dimension_type", sa.String(length=32), nullable=False),
        sa.Column("query", sa.Text(), nullable=True),
        sa.Column("page_url", sa.String(length=2048), nullable=True),
        sa.Column("country", sa.String(length=8), nullable=True),
        sa.Column("device", sa.String(length=16), nullable=True),
        sa.Column("start_date", sa.Date(), nullable=False),
        sa.Column("end_date", sa.Date(), nullable=False),
        sa.Column("clicks", sa.Float(), nullable=False, server_default="0"),
        sa.Column("impressions", sa.Float(), nullable=False, server_default="0"),
        sa.Column("ctr", sa.Float(), nullable=False, server_default="0"),
        sa.Column("average_position", sa.Float(), nullable=False, server_default="0"),
        sa.Column("compare_clicks", sa.Float(), nullable=True),
        sa.Column("compare_impressions", sa.Float(), nullable=True),
        sa.Column("compare_ctr", sa.Float(), nullable=True),
        sa.Column("compare_position", sa.Float(), nullable=True),
        sa.Column("metrics_delta", sa.JSON(), nullable=False),
        sa.Column("row_key", sa.String(length=512), nullable=False),
        sa.Column("data_source", sa.String(length=32), nullable=False, server_default="search_console_api"),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["sync_id"], ["search_console_syncs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("sync_id", "row_key", name="uq_search_console_perf_sync_row"),
    )
    op.create_index("ix_search_console_performance_rows_sync_id", "search_console_performance_rows", ["sync_id"])
    op.create_index("ix_search_console_performance_rows_organization_id", "search_console_performance_rows", ["organization_id"])
    op.create_index("ix_search_console_performance_rows_dimension_type", "search_console_performance_rows", ["dimension_type"])

    op.create_table(
        "search_console_opportunities",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("sync_id", sa.UUID(), nullable=False),
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.Column("site_url", sa.String(length=2048), nullable=False),
        sa.Column("opportunity_type", sa.String(length=64), nullable=False),
        sa.Column("rule_id", sa.String(length=64), nullable=False),
        sa.Column("priority", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="open"),
        sa.Column("query", sa.Text(), nullable=True),
        sa.Column("page_url", sa.String(length=2048), nullable=True),
        sa.Column("date_range_start", sa.Date(), nullable=False),
        sa.Column("date_range_end", sa.Date(), nullable=False),
        sa.Column("clicks", sa.Float(), nullable=False, server_default="0"),
        sa.Column("impressions", sa.Float(), nullable=False, server_default="0"),
        sa.Column("ctr", sa.Float(), nullable=False, server_default="0"),
        sa.Column("average_position", sa.Float(), nullable=False, server_default="0"),
        sa.Column("comparison_metrics", sa.JSON(), nullable=False),
        sa.Column("evidence", sa.JSON(), nullable=False),
        sa.Column("explanation", sa.Text(), nullable=False),
        sa.Column("dedupe_key", sa.String(length=512), nullable=False),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["sync_id"], ["search_console_syncs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("sync_id", "dedupe_key", name="uq_search_console_opps_sync_dedupe"),
    )
    op.create_index("ix_search_console_opportunities_sync_id", "search_console_opportunities", ["sync_id"])
    op.create_index("ix_search_console_opportunities_organization_id", "search_console_opportunities", ["organization_id"])
    op.create_index("ix_search_console_opportunities_rule_id", "search_console_opportunities", ["rule_id"])
    op.create_index("ix_search_console_opportunities_priority", "search_console_opportunities", ["priority"])


def downgrade() -> None:
    op.drop_index("ix_search_console_opportunities_priority", table_name="search_console_opportunities")
    op.drop_index("ix_search_console_opportunities_rule_id", table_name="search_console_opportunities")
    op.drop_index("ix_search_console_opportunities_organization_id", table_name="search_console_opportunities")
    op.drop_index("ix_search_console_opportunities_sync_id", table_name="search_console_opportunities")
    op.drop_table("search_console_opportunities")
    op.drop_index("ix_search_console_performance_rows_dimension_type", table_name="search_console_performance_rows")
    op.drop_index("ix_search_console_performance_rows_organization_id", table_name="search_console_performance_rows")
    op.drop_index("ix_search_console_performance_rows_sync_id", table_name="search_console_performance_rows")
    op.drop_table("search_console_performance_rows")
    op.drop_index("ix_search_console_syncs_org_status", table_name="search_console_syncs")
    op.drop_index("ix_search_console_syncs_status", table_name="search_console_syncs")
    op.drop_index("ix_search_console_syncs_organization_id", table_name="search_console_syncs")
    op.drop_table("search_console_syncs")
