"""SEO internal-link engine (M9.12).

Revision ID: b2c9d4e5f106
Revises: f1a8c3d2e904
Create Date: 2026-09-27
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "b2c9d4e5f106"
down_revision: Union[str, None] = "f1a8c3d2e904"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "seo_internal_link_runs",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.Column("generated_content_id", sa.UUID(), nullable=True),
        sa.Column("content_brief_id", sa.UUID(), nullable=True),
        sa.Column("crawl_id", sa.UUID(), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="completed"),
        sa.Column("analysis_key", sa.String(length=64), nullable=False),
        sa.Column("stats", sa.JSON(), nullable=False),
        sa.Column("limitations", sa.JSON(), nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=False, server_default="none"),
        sa.Column("model", sa.String(length=64), nullable=False, server_default="none"),
        sa.Column("prompt_version", sa.String(length=32), nullable=False, server_default="internal_link_prompt_v1"),
        sa.Column("algorithm_version", sa.String(length=32), nullable=False, server_default="internal_link_engine_v1"),
        sa.Column("ai_enriched", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["generated_content_id"], ["seo_generated_content.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["content_brief_id"], ["seo_content_briefs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["crawl_id"], ["seo_crawls.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("organization_id", "analysis_key", name="uq_seo_internal_link_runs_org_analysis_key"),
    )
    op.create_index("ix_seo_internal_link_runs_organization_id", "seo_internal_link_runs", ["organization_id"])
    op.create_index("ix_seo_internal_link_runs_generated_content_id", "seo_internal_link_runs", ["generated_content_id"])
    op.create_index("ix_seo_internal_link_runs_content_brief_id", "seo_internal_link_runs", ["content_brief_id"])
    op.create_index("ix_seo_internal_link_runs_crawl_id", "seo_internal_link_runs", ["crawl_id"])
    op.create_index("ix_seo_internal_link_runs_status", "seo_internal_link_runs", ["status"])
    op.create_index("ix_seo_internal_link_runs_analysis_key", "seo_internal_link_runs", ["analysis_key"])
    op.create_index("ix_seo_internal_link_runs_algorithm_version", "seo_internal_link_runs", ["algorithm_version"])

    op.create_table(
        "seo_internal_link_opportunities",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.Column("run_id", sa.UUID(), nullable=False),
        sa.Column("generated_content_id", sa.UUID(), nullable=True),
        sa.Column("content_brief_id", sa.UUID(), nullable=True),
        sa.Column("source_url", sa.String(length=2048), nullable=False),
        sa.Column("target_url", sa.String(length=2048), nullable=False),
        sa.Column("source_crawl_page_id", sa.UUID(), nullable=True),
        sa.Column("target_crawl_page_id", sa.UUID(), nullable=True),
        sa.Column("anchor_text", sa.String(length=255), nullable=False),
        sa.Column("anchor_alternatives", sa.JSON(), nullable=False),
        sa.Column("opportunity_type", sa.String(length=64), nullable=False),
        sa.Column("relationship_reason", sa.Text(), nullable=False),
        sa.Column("source_topic", sa.String(length=255), nullable=True),
        sa.Column("target_topic", sa.String(length=255), nullable=True),
        sa.Column("source_keywords", sa.JSON(), nullable=False),
        sa.Column("target_keywords", sa.JSON(), nullable=False),
        sa.Column("relevance_score", sa.Float(), nullable=False),
        sa.Column("confidence", sa.String(length=16), nullable=False),
        sa.Column("score_breakdown", sa.JSON(), nullable=False),
        sa.Column("evidence_refs", sa.JSON(), nullable=False),
        sa.Column("limitations", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="suggested"),
        sa.Column("dedupe_key", sa.String(length=256), nullable=False),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["run_id"], ["seo_internal_link_runs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["generated_content_id"], ["seo_generated_content.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["content_brief_id"], ["seo_content_briefs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["source_crawl_page_id"], ["seo_crawl_pages.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["target_crawl_page_id"], ["seo_crawl_pages.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("organization_id", "run_id", "dedupe_key", name="uq_seo_internal_link_opps_run_dedupe"),
    )
    op.create_index("ix_seo_internal_link_opportunities_organization_id", "seo_internal_link_opportunities", ["organization_id"])
    op.create_index("ix_seo_internal_link_opportunities_run_id", "seo_internal_link_opportunities", ["run_id"])
    op.create_index("ix_seo_internal_link_opportunities_generated_content_id", "seo_internal_link_opportunities", ["generated_content_id"])
    op.create_index("ix_seo_internal_link_opportunities_content_brief_id", "seo_internal_link_opportunities", ["content_brief_id"])
    op.create_index("ix_seo_internal_link_opportunities_source_url", "seo_internal_link_opportunities", ["source_url"])
    op.create_index("ix_seo_internal_link_opportunities_target_url", "seo_internal_link_opportunities", ["target_url"])
    op.create_index("ix_seo_internal_link_opportunities_opportunity_type", "seo_internal_link_opportunities", ["opportunity_type"])
    op.create_index("ix_seo_internal_link_opportunities_relevance_score", "seo_internal_link_opportunities", ["relevance_score"])
    op.create_index("ix_seo_internal_link_opportunities_confidence", "seo_internal_link_opportunities", ["confidence"])
    op.create_index("ix_seo_internal_link_opportunities_status", "seo_internal_link_opportunities", ["status"])
    op.create_index("ix_seo_internal_link_opportunities_dedupe_key", "seo_internal_link_opportunities", ["dedupe_key"])


def downgrade() -> None:
    op.drop_index("ix_seo_internal_link_opportunities_dedupe_key", table_name="seo_internal_link_opportunities")
    op.drop_index("ix_seo_internal_link_opportunities_status", table_name="seo_internal_link_opportunities")
    op.drop_index("ix_seo_internal_link_opportunities_confidence", table_name="seo_internal_link_opportunities")
    op.drop_index("ix_seo_internal_link_opportunities_relevance_score", table_name="seo_internal_link_opportunities")
    op.drop_index("ix_seo_internal_link_opportunities_opportunity_type", table_name="seo_internal_link_opportunities")
    op.drop_index("ix_seo_internal_link_opportunities_target_url", table_name="seo_internal_link_opportunities")
    op.drop_index("ix_seo_internal_link_opportunities_source_url", table_name="seo_internal_link_opportunities")
    op.drop_index("ix_seo_internal_link_opportunities_content_brief_id", table_name="seo_internal_link_opportunities")
    op.drop_index("ix_seo_internal_link_opportunities_generated_content_id", table_name="seo_internal_link_opportunities")
    op.drop_index("ix_seo_internal_link_opportunities_run_id", table_name="seo_internal_link_opportunities")
    op.drop_index("ix_seo_internal_link_opportunities_organization_id", table_name="seo_internal_link_opportunities")
    op.drop_table("seo_internal_link_opportunities")

    op.drop_index("ix_seo_internal_link_runs_algorithm_version", table_name="seo_internal_link_runs")
    op.drop_index("ix_seo_internal_link_runs_analysis_key", table_name="seo_internal_link_runs")
    op.drop_index("ix_seo_internal_link_runs_status", table_name="seo_internal_link_runs")
    op.drop_index("ix_seo_internal_link_runs_crawl_id", table_name="seo_internal_link_runs")
    op.drop_index("ix_seo_internal_link_runs_content_brief_id", table_name="seo_internal_link_runs")
    op.drop_index("ix_seo_internal_link_runs_generated_content_id", table_name="seo_internal_link_runs")
    op.drop_index("ix_seo_internal_link_runs_organization_id", table_name="seo_internal_link_runs")
    op.drop_table("seo_internal_link_runs")
