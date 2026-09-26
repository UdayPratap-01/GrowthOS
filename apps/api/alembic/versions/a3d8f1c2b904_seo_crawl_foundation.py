"""SEO crawl foundation — crawl runs and page observations.

Revision ID: a3d8f1c2b904
Revises: f7c2e9a1b834
Create Date: 2026-09-27
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "a3d8f1c2b904"
down_revision: Union[str, None] = "f7c2e9a1b834"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "seo_crawls",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.Column("client_id", sa.UUID(), nullable=True),
        sa.Column("root_url", sa.String(length=2048), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("config", sa.JSON(), nullable=False),
        sa.Column("stats", sa.JSON(), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("cancel_requested", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("job_id", sa.UUID(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["client_id"], ["clients.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["job_id"], ["background_jobs.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_seo_crawls_organization_id", "seo_crawls", ["organization_id"])
    op.create_index("ix_seo_crawls_client_id", "seo_crawls", ["client_id"])
    op.create_index("ix_seo_crawls_status", "seo_crawls", ["status"])
    op.create_index("ix_seo_crawls_org_status", "seo_crawls", ["organization_id", "status"])

    op.create_table(
        "seo_crawl_pages",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("crawl_id", sa.UUID(), nullable=False),
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.Column("url", sa.String(length=2048), nullable=False),
        sa.Column("final_url", sa.String(length=2048), nullable=True),
        sa.Column("depth", sa.Integer(), nullable=False),
        sa.Column("referrer_url", sa.String(length=2048), nullable=True),
        sa.Column("http_status", sa.Integer(), nullable=True),
        sa.Column("content_type", sa.String(length=255), nullable=True),
        sa.Column("response_bytes", sa.Integer(), nullable=False),
        sa.Column("redirect_count", sa.Integer(), nullable=False),
        sa.Column("response_time_ms", sa.Float(), nullable=True),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("observations", sa.JSON(), nullable=False),
        sa.Column("data_source", sa.String(length=32), nullable=False),
        sa.ForeignKeyConstraint(["crawl_id"], ["seo_crawls.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_seo_crawl_pages_crawl_id", "seo_crawl_pages", ["crawl_id"])
    op.create_index("ix_seo_crawl_pages_organization_id", "seo_crawl_pages", ["organization_id"])
    op.create_index("ix_seo_crawl_pages_crawl_url", "seo_crawl_pages", ["crawl_id", "url"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_seo_crawl_pages_crawl_url", table_name="seo_crawl_pages")
    op.drop_index("ix_seo_crawl_pages_organization_id", table_name="seo_crawl_pages")
    op.drop_index("ix_seo_crawl_pages_crawl_id", table_name="seo_crawl_pages")
    op.drop_table("seo_crawl_pages")
    op.drop_index("ix_seo_crawls_org_status", table_name="seo_crawls")
    op.drop_index("ix_seo_crawls_status", table_name="seo_crawls")
    op.drop_index("ix_seo_crawls_client_id", table_name="seo_crawls")
    op.drop_index("ix_seo_crawls_organization_id", table_name="seo_crawls")
    op.drop_table("seo_crawls")
