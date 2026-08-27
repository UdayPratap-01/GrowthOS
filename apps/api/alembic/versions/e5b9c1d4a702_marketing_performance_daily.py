"""Normalized marketing performance daily table.

Revision ID: e5b9c1d4a702
Revises: d4e8f2a1b6c0
Create Date: 2026-08-27
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "e5b9c1d4a702"
down_revision: Union[str, None] = "d4e8f2a1b6c0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "marketing_performance_daily",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.Column("client_id", sa.UUID(), nullable=True),
        sa.Column("platform", sa.String(length=64), nullable=False),
        sa.Column("entity_level", sa.String(length=32), nullable=False),
        sa.Column("external_account_id", sa.String(length=255), nullable=False),
        sa.Column("external_campaign_id", sa.String(length=255), nullable=False),
        sa.Column("external_ad_set_id", sa.String(length=255), nullable=False),
        sa.Column("external_ad_id", sa.String(length=255), nullable=False),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("granularity", sa.String(length=16), nullable=False),
        sa.Column("impressions", sa.Integer(), nullable=False),
        sa.Column("reach", sa.Integer(), nullable=True),
        sa.Column("clicks", sa.Integer(), nullable=False),
        sa.Column("spend", sa.Numeric(precision=14, scale=4), nullable=False),
        sa.Column("conversions", sa.Numeric(precision=14, scale=4), nullable=False),
        sa.Column("leads", sa.Integer(), nullable=False),
        sa.Column("revenue", sa.Numeric(precision=14, scale=4), nullable=False),
        sa.Column("ctr", sa.Numeric(precision=12, scale=6), nullable=True),
        sa.Column("cpc", sa.Numeric(precision=14, scale=6), nullable=True),
        sa.Column("cpm", sa.Numeric(precision=14, scale=6), nullable=True),
        sa.Column("cpl", sa.Numeric(precision=14, scale=6), nullable=True),
        sa.Column("cpa", sa.Numeric(precision=14, scale=6), nullable=True),
        sa.Column("roas", sa.Numeric(precision=14, scale=6), nullable=True),
        sa.Column("currency", sa.String(length=8), nullable=False),
        sa.Column("provider_metadata", sa.JSON(), nullable=False),
        sa.Column("data_source", sa.Enum("demo", "live", name="marketing_perf_daily_source", native_enum=False), nullable=False),
        sa.Column("ingested_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["client_id"], ["clients.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "organization_id",
            "platform",
            "entity_level",
            "external_account_id",
            "external_campaign_id",
            "external_ad_set_id",
            "external_ad_id",
            "date",
            "granularity",
            name="uq_marketing_perf_daily_natural_key",
        ),
    )
    op.create_index("ix_marketing_performance_daily_organization_id", "marketing_performance_daily", ["organization_id"])
    op.create_index("ix_marketing_performance_daily_client_id", "marketing_performance_daily", ["client_id"])
    op.create_index("ix_marketing_performance_daily_platform", "marketing_performance_daily", ["platform"])
    op.create_index("ix_marketing_performance_daily_date", "marketing_performance_daily", ["date"])
    op.create_index("ix_marketing_perf_daily_org_date", "marketing_performance_daily", ["organization_id", "date"])
    op.create_index(
        "ix_marketing_perf_daily_org_platform_date",
        "marketing_performance_daily",
        ["organization_id", "platform", "date"],
    )
    op.create_index(
        "ix_marketing_perf_daily_org_client_date",
        "marketing_performance_daily",
        ["organization_id", "client_id", "date"],
    )
    op.create_index(
        "ix_marketing_perf_daily_org_ext_campaign",
        "marketing_performance_daily",
        ["organization_id", "platform", "external_campaign_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_marketing_perf_daily_org_ext_campaign", table_name="marketing_performance_daily")
    op.drop_index("ix_marketing_perf_daily_org_client_date", table_name="marketing_performance_daily")
    op.drop_index("ix_marketing_perf_daily_org_platform_date", table_name="marketing_performance_daily")
    op.drop_index("ix_marketing_perf_daily_org_date", table_name="marketing_performance_daily")
    op.drop_index("ix_marketing_performance_daily_date", table_name="marketing_performance_daily")
    op.drop_index("ix_marketing_performance_daily_platform", table_name="marketing_performance_daily")
    op.drop_index("ix_marketing_performance_daily_client_id", table_name="marketing_performance_daily")
    op.drop_index("ix_marketing_performance_daily_organization_id", table_name="marketing_performance_daily")
    op.drop_table("marketing_performance_daily")
