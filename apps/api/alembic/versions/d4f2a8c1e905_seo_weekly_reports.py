"""SEO weekly reports (M9.16).

Revision ID: d4f2a8c1e905
Revises: c3a8f1e2b904
Create Date: 2026-09-28
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "d4f2a8c1e905"
down_revision: Union[str, None] = "c3a8f1e2b904"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "seo_report_config",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.Column("reporting_enabled", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("last_report_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_failure_reason", sa.String(length=512), nullable=True),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("organization_id", name="uq_seo_report_config_org"),
    )
    op.create_index("ix_seo_report_config_organization_id", "seo_report_config", ["organization_id"])

    op.create_table(
        "seo_weekly_reports",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.Column("period_start", sa.Date(), nullable=False),
        sa.Column("period_end", sa.Date(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="queued"),
        sa.Column("report_version", sa.String(length=16), nullable=False, server_default="v1"),
        sa.Column("generated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("data_freshness", sa.JSON(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False, server_default=""),
        sa.Column("report_payload", sa.JSON(), nullable=False),
        sa.Column("limitations", sa.JSON(), nullable=False),
        sa.Column("error_message", sa.String(length=512), nullable=True),
        sa.Column("background_job_id", sa.UUID(), nullable=True),
        sa.Column("generation_key", sa.String(length=128), nullable=False),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "organization_id",
            "period_start",
            "period_end",
            "report_version",
            name="uq_seo_weekly_reports_org_period_version",
        ),
    )
    op.create_index("ix_seo_weekly_reports_organization_id", "seo_weekly_reports", ["organization_id"])
    op.create_index("ix_seo_weekly_reports_status", "seo_weekly_reports", ["status"])
    op.create_index("ix_seo_weekly_reports_generation_key", "seo_weekly_reports", ["generation_key"])
    op.create_index("ix_seo_weekly_reports_org_created", "seo_weekly_reports", ["organization_id", "created_at"])
    op.create_index("ix_seo_weekly_reports_org_status", "seo_weekly_reports", ["organization_id", "status"])


def downgrade() -> None:
    op.drop_table("seo_weekly_reports")
    op.drop_table("seo_report_config")
