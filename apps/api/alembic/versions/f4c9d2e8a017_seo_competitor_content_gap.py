"""SEO competitor and content-gap analysis (M9.6).

Revision ID: f4c9d2e8a017
Revises: e2b6c4d9f018
Create Date: 2026-09-27
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "f4c9d2e8a017"
down_revision: Union[str, None] = "e2b6c4d9f018"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "seo_competitors",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.Column("display_name", sa.String(length=255), nullable=True),
        sa.Column("root_url", sa.String(length=2048), nullable=False),
        sa.Column("domain", sa.String(length=512), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="active"),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("organization_id", "root_url", name="uq_seo_competitors_org_root_url"),
    )
    op.create_index("ix_seo_competitors_organization_id", "seo_competitors", ["organization_id"])
    op.create_index("ix_seo_competitors_root_url", "seo_competitors", ["root_url"])
    op.create_index("ix_seo_competitors_domain", "seo_competitors", ["domain"])
    op.create_index("ix_seo_competitors_status", "seo_competitors", ["status"])

    op.create_table(
        "seo_competitor_crawls",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("competitor_id", sa.UUID(), nullable=False),
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.Column("root_url", sa.String(length=2048), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="queued"),
        sa.Column("config", sa.JSON(), nullable=False),
        sa.Column("stats", sa.JSON(), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("cancel_requested", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("job_id", sa.UUID(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["competitor_id"], ["seo_competitors.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["job_id"], ["background_jobs.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_seo_competitor_crawls_competitor_id", "seo_competitor_crawls", ["competitor_id"])
    op.create_index("ix_seo_competitor_crawls_organization_id", "seo_competitor_crawls", ["organization_id"])
    op.create_index("ix_seo_competitor_crawls_status", "seo_competitor_crawls", ["status"])

    op.create_table(
        "seo_competitor_pages",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("crawl_id", sa.UUID(), nullable=False),
        sa.Column("competitor_id", sa.UUID(), nullable=False),
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.Column("url", sa.String(length=2048), nullable=False),
        sa.Column("final_url", sa.String(length=2048), nullable=True),
        sa.Column("depth", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("http_status", sa.Integer(), nullable=True),
        sa.Column("content_type", sa.String(length=255), nullable=True),
        sa.Column("title", sa.Text(), nullable=True),
        sa.Column("meta_description", sa.Text(), nullable=True),
        sa.Column("h1", sa.Text(), nullable=True),
        sa.Column("headings", sa.JSON(), nullable=False),
        sa.Column("topic_label", sa.String(length=512), nullable=True),
        sa.Column("token_terms", sa.JSON(), nullable=False),
        sa.Column("word_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("structured_data_present", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("observations", sa.JSON(), nullable=False),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.ForeignKeyConstraint(["competitor_id"], ["seo_competitors.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["crawl_id"], ["seo_competitor_crawls.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("crawl_id", "url", name="uq_seo_competitor_pages_crawl_url"),
    )
    op.create_index("ix_seo_competitor_pages_crawl_id", "seo_competitor_pages", ["crawl_id"])
    op.create_index("ix_seo_competitor_pages_competitor_id", "seo_competitor_pages", ["competitor_id"])
    op.create_index("ix_seo_competitor_pages_organization_id", "seo_competitor_pages", ["organization_id"])
    op.create_index("ix_seo_competitor_pages_url", "seo_competitor_pages", ["url"])

    op.create_table(
        "content_gap_analysis_runs",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.Column("sync_id", sa.UUID(), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="completed"),
        sa.Column("algorithm_version", sa.String(length=32), nullable=False, server_default="competitor_gap_v1"),
        sa.Column("stats", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["sync_id"], ["search_console_syncs.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_content_gap_analysis_runs_organization_id", "content_gap_analysis_runs", ["organization_id"])
    op.create_index("ix_content_gap_analysis_runs_sync_id", "content_gap_analysis_runs", ["sync_id"])
    op.create_index("ix_content_gap_analysis_runs_algorithm_version", "content_gap_analysis_runs", ["algorithm_version"])
    op.create_index("ix_content_gap_analysis_runs_status", "content_gap_analysis_runs", ["status"])

    op.create_table(
        "content_gaps",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.Column("analysis_run_id", sa.UUID(), nullable=False),
        sa.Column("sync_id", sa.UUID(), nullable=True),
        sa.Column("competitor_id", sa.UUID(), nullable=True),
        sa.Column("competitor_crawl_id", sa.UUID(), nullable=True),
        sa.Column("competitor_page_id", sa.UUID(), nullable=True),
        sa.Column("competitor_url", sa.String(length=2048), nullable=True),
        sa.Column("gap_type", sa.String(length=64), nullable=False),
        sa.Column("topic_label", sa.String(length=512), nullable=False),
        sa.Column("user_topic_id", sa.UUID(), nullable=True),
        sa.Column("user_query", sa.Text(), nullable=True),
        sa.Column("competitor_title", sa.Text(), nullable=True),
        sa.Column("competitor_h1", sa.Text(), nullable=True),
        sa.Column("similarity", sa.Float(), nullable=False, server_default="0"),
        sa.Column("match_strength", sa.String(length=32), nullable=False, server_default="weak_match"),
        sa.Column("evidence", sa.JSON(), nullable=False),
        sa.Column("explanation", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="open"),
        sa.Column("algorithm_version", sa.String(length=32), nullable=False, server_default="competitor_gap_v1"),
        sa.Column("dedupe_key", sa.String(length=512), nullable=False),
        sa.ForeignKeyConstraint(["analysis_run_id"], ["content_gap_analysis_runs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["competitor_crawl_id"], ["seo_competitor_crawls.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["competitor_id"], ["seo_competitors.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["competitor_page_id"], ["seo_competitor_pages.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["sync_id"], ["search_console_syncs.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["user_topic_id"], ["topic_clusters.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("organization_id", "dedupe_key", name="uq_content_gaps_org_dedupe"),
    )
    op.create_index("ix_content_gaps_organization_id", "content_gaps", ["organization_id"])
    op.create_index("ix_content_gaps_analysis_run_id", "content_gaps", ["analysis_run_id"])
    op.create_index("ix_content_gaps_sync_id", "content_gaps", ["sync_id"])
    op.create_index("ix_content_gaps_competitor_id", "content_gaps", ["competitor_id"])
    op.create_index("ix_content_gaps_gap_type", "content_gaps", ["gap_type"])
    op.create_index("ix_content_gaps_user_topic_id", "content_gaps", ["user_topic_id"])
    op.create_index("ix_content_gaps_match_strength", "content_gaps", ["match_strength"])
    op.create_index("ix_content_gaps_status", "content_gaps", ["status"])
    op.create_index("ix_content_gaps_algorithm_version", "content_gaps", ["algorithm_version"])


def downgrade() -> None:
    op.drop_index("ix_content_gaps_algorithm_version", table_name="content_gaps")
    op.drop_index("ix_content_gaps_status", table_name="content_gaps")
    op.drop_index("ix_content_gaps_match_strength", table_name="content_gaps")
    op.drop_index("ix_content_gaps_user_topic_id", table_name="content_gaps")
    op.drop_index("ix_content_gaps_gap_type", table_name="content_gaps")
    op.drop_index("ix_content_gaps_competitor_id", table_name="content_gaps")
    op.drop_index("ix_content_gaps_sync_id", table_name="content_gaps")
    op.drop_index("ix_content_gaps_analysis_run_id", table_name="content_gaps")
    op.drop_index("ix_content_gaps_organization_id", table_name="content_gaps")
    op.drop_table("content_gaps")
    op.drop_index("ix_content_gap_analysis_runs_status", table_name="content_gap_analysis_runs")
    op.drop_index("ix_content_gap_analysis_runs_algorithm_version", table_name="content_gap_analysis_runs")
    op.drop_index("ix_content_gap_analysis_runs_sync_id", table_name="content_gap_analysis_runs")
    op.drop_index("ix_content_gap_analysis_runs_organization_id", table_name="content_gap_analysis_runs")
    op.drop_table("content_gap_analysis_runs")
    op.drop_index("ix_seo_competitor_pages_url", table_name="seo_competitor_pages")
    op.drop_index("ix_seo_competitor_pages_organization_id", table_name="seo_competitor_pages")
    op.drop_index("ix_seo_competitor_pages_competitor_id", table_name="seo_competitor_pages")
    op.drop_index("ix_seo_competitor_pages_crawl_id", table_name="seo_competitor_pages")
    op.drop_table("seo_competitor_pages")
    op.drop_index("ix_seo_competitor_crawls_status", table_name="seo_competitor_crawls")
    op.drop_index("ix_seo_competitor_crawls_organization_id", table_name="seo_competitor_crawls")
    op.drop_index("ix_seo_competitor_crawls_competitor_id", table_name="seo_competitor_crawls")
    op.drop_table("seo_competitor_crawls")
    op.drop_index("ix_seo_competitors_status", table_name="seo_competitors")
    op.drop_index("ix_seo_competitors_domain", table_name="seo_competitors")
    op.drop_index("ix_seo_competitors_root_url", table_name="seo_competitors")
    op.drop_index("ix_seo_competitors_organization_id", table_name="seo_competitors")
    op.drop_table("seo_competitors")
