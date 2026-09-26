"""Keyword opportunity engine — deterministic keyword opportunities from GSC data.

Revision ID: d1a5f3c8e947
Revises: c9d4e1f2a836
Create Date: 2026-09-27
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "d1a5f3c8e947"
down_revision: Union[str, None] = "c9d4e1f2a836"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "keyword_opportunities",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("sync_id", sa.UUID(), nullable=False),
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.Column("site_url", sa.String(length=2048), nullable=False),
        sa.Column("query", sa.Text(), nullable=False),
        sa.Column("normalized_query", sa.String(length=1024), nullable=False),
        sa.Column("page_url", sa.String(length=2048), nullable=True),
        sa.Column("opportunity_type", sa.String(length=64), nullable=False),
        sa.Column("rule_id", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="open"),
        sa.Column("priority", sa.String(length=16), nullable=False),
        sa.Column("priority_score", sa.Float(), nullable=False, server_default="0"),
        sa.Column("clicks", sa.Float(), nullable=False, server_default="0"),
        sa.Column("impressions", sa.Float(), nullable=False, server_default="0"),
        sa.Column("ctr", sa.Float(), nullable=False, server_default="0"),
        sa.Column("average_position", sa.Float(), nullable=False, server_default="0"),
        sa.Column("previous_clicks", sa.Float(), nullable=True),
        sa.Column("previous_impressions", sa.Float(), nullable=True),
        sa.Column("previous_ctr", sa.Float(), nullable=True),
        sa.Column("previous_average_position", sa.Float(), nullable=True),
        sa.Column("change_metrics", sa.JSON(), nullable=False),
        sa.Column("query_classification", sa.JSON(), nullable=False),
        sa.Column("page_associations", sa.JSON(), nullable=False),
        sa.Column("evidence", sa.JSON(), nullable=False),
        sa.Column("explanation", sa.Text(), nullable=False),
        sa.Column("date_start", sa.Date(), nullable=False),
        sa.Column("date_end", sa.Date(), nullable=False),
        sa.Column("dedupe_key", sa.String(length=512), nullable=False),
        sa.Column("data_source", sa.String(length=32), nullable=False, server_default="search_console_performance"),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["sync_id"], ["search_console_syncs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("sync_id", "dedupe_key", name="uq_keyword_opportunities_sync_dedupe"),
    )
    op.create_index("ix_keyword_opportunities_sync_id", "keyword_opportunities", ["sync_id"])
    op.create_index("ix_keyword_opportunities_organization_id", "keyword_opportunities", ["organization_id"])
    op.create_index("ix_keyword_opportunities_site_url", "keyword_opportunities", ["site_url"])
    op.create_index("ix_keyword_opportunities_normalized_query", "keyword_opportunities", ["normalized_query"])
    op.create_index("ix_keyword_opportunities_page_url", "keyword_opportunities", ["page_url"])
    op.create_index("ix_keyword_opportunities_opportunity_type", "keyword_opportunities", ["opportunity_type"])
    op.create_index("ix_keyword_opportunities_rule_id", "keyword_opportunities", ["rule_id"])
    op.create_index("ix_keyword_opportunities_status", "keyword_opportunities", ["status"])
    op.create_index("ix_keyword_opportunities_priority", "keyword_opportunities", ["priority"])


def downgrade() -> None:
    op.drop_index("ix_keyword_opportunities_priority", table_name="keyword_opportunities")
    op.drop_index("ix_keyword_opportunities_status", table_name="keyword_opportunities")
    op.drop_index("ix_keyword_opportunities_rule_id", table_name="keyword_opportunities")
    op.drop_index("ix_keyword_opportunities_opportunity_type", table_name="keyword_opportunities")
    op.drop_index("ix_keyword_opportunities_page_url", table_name="keyword_opportunities")
    op.drop_index("ix_keyword_opportunities_normalized_query", table_name="keyword_opportunities")
    op.drop_index("ix_keyword_opportunities_site_url", table_name="keyword_opportunities")
    op.drop_index("ix_keyword_opportunities_organization_id", table_name="keyword_opportunities")
    op.drop_index("ix_keyword_opportunities_sync_id", table_name="keyword_opportunities")
    op.drop_table("keyword_opportunities")
