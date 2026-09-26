"""SEO analysis findings — deterministic technical SEO findings.

Revision ID: b7e4a2c9d015
Revises: a3d8f1c2b904
Create Date: 2026-09-27
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "b7e4a2c9d015"
down_revision: Union[str, None] = "a3d8f1c2b904"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "seo_findings",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("crawl_id", sa.UUID(), nullable=False),
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.Column("page_id", sa.UUID(), nullable=True),
        sa.Column("rule_id", sa.String(length=64), nullable=False),
        sa.Column("category", sa.String(length=32), nullable=False),
        sa.Column("severity", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="open"),
        sa.Column("dedupe_key", sa.String(length=512), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("evidence", sa.JSON(), nullable=False),
        sa.Column("observed_value", sa.Text(), nullable=True),
        sa.Column("expected_or_heuristic", sa.Text(), nullable=True),
        sa.Column("recommendation", sa.Text(), nullable=True),
        sa.Column("url", sa.String(length=2048), nullable=True),
        sa.ForeignKeyConstraint(["crawl_id"], ["seo_crawls.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["page_id"], ["seo_crawl_pages.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("crawl_id", "dedupe_key", name="uq_seo_findings_crawl_dedupe"),
    )
    op.create_index("ix_seo_findings_crawl_id", "seo_findings", ["crawl_id"])
    op.create_index("ix_seo_findings_organization_id", "seo_findings", ["organization_id"])
    op.create_index("ix_seo_findings_page_id", "seo_findings", ["page_id"])
    op.create_index("ix_seo_findings_rule_id", "seo_findings", ["rule_id"])
    op.create_index("ix_seo_findings_category", "seo_findings", ["category"])
    op.create_index("ix_seo_findings_severity", "seo_findings", ["severity"])
    op.create_index("ix_seo_findings_crawl_severity", "seo_findings", ["crawl_id", "severity"])


def downgrade() -> None:
    op.drop_index("ix_seo_findings_crawl_severity", table_name="seo_findings")
    op.drop_index("ix_seo_findings_severity", table_name="seo_findings")
    op.drop_index("ix_seo_findings_category", table_name="seo_findings")
    op.drop_index("ix_seo_findings_rule_id", table_name="seo_findings")
    op.drop_index("ix_seo_findings_page_id", table_name="seo_findings")
    op.drop_index("ix_seo_findings_organization_id", table_name="seo_findings")
    op.drop_index("ix_seo_findings_crawl_id", table_name="seo_findings")
    op.drop_table("seo_findings")
