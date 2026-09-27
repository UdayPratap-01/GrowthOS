"""SEO content briefs (M9.8).

Revision ID: b6e4f2a9c130
Revises: a5d3e8f1b029
Create Date: 2026-09-27
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "b6e4f2a9c130"
down_revision: Union[str, None] = "a5d3e8f1b029"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "seo_content_briefs",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.Column("recommendation_id", sa.UUID(), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("brief_type", sa.String(length=64), nullable=False),
        sa.Column("primary_keyword", sa.String(length=255), nullable=False),
        sa.Column("secondary_keywords", sa.JSON(), nullable=False),
        sa.Column("target_topic", sa.String(length=512), nullable=True),
        sa.Column("search_intent", sa.JSON(), nullable=False),
        sa.Column("target_url", sa.String(length=2048), nullable=True),
        sa.Column("content_goal", sa.Text(), nullable=False),
        sa.Column("target_audience", sa.Text(), nullable=False),
        sa.Column("suggested_content_type", sa.String(length=128), nullable=False),
        sa.Column("suggested_angle", sa.Text(), nullable=False),
        sa.Column("outline", sa.JSON(), nullable=False),
        sa.Column("questions_to_answer", sa.JSON(), nullable=False),
        sa.Column("entities_to_cover", sa.JSON(), nullable=False),
        sa.Column("internal_link_targets", sa.JSON(), nullable=False),
        sa.Column("competitor_context", sa.JSON(), nullable=False),
        sa.Column("evidence_refs", sa.JSON(), nullable=False),
        sa.Column("source_recommendation_ids", sa.JSON(), nullable=False),
        sa.Column("content_requirements", sa.JSON(), nullable=False),
        sa.Column("seo_requirements", sa.JSON(), nullable=False),
        sa.Column("limitations", sa.JSON(), nullable=False),
        sa.Column("context_snapshot", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="draft"),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("model", sa.String(length=64), nullable=False),
        sa.Column("prompt_version", sa.String(length=32), nullable=False, server_default="seo_content_brief_prompt_v1"),
        sa.Column("algorithm_version", sa.String(length=32), nullable=False, server_default="seo_content_brief_v1"),
        sa.Column("generation_key", sa.String(length=64), nullable=False),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["recommendation_id"], ["seo_recommendations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("organization_id", "generation_key", name="uq_seo_content_briefs_org_gen_key"),
    )
    op.create_index("ix_seo_content_briefs_organization_id", "seo_content_briefs", ["organization_id"])
    op.create_index("ix_seo_content_briefs_recommendation_id", "seo_content_briefs", ["recommendation_id"])
    op.create_index("ix_seo_content_briefs_brief_type", "seo_content_briefs", ["brief_type"])
    op.create_index("ix_seo_content_briefs_status", "seo_content_briefs", ["status"])
    op.create_index("ix_seo_content_briefs_prompt_version", "seo_content_briefs", ["prompt_version"])
    op.create_index("ix_seo_content_briefs_algorithm_version", "seo_content_briefs", ["algorithm_version"])
    op.create_index("ix_seo_content_briefs_generation_key", "seo_content_briefs", ["generation_key"])


def downgrade() -> None:
    op.drop_index("ix_seo_content_briefs_generation_key", table_name="seo_content_briefs")
    op.drop_index("ix_seo_content_briefs_algorithm_version", table_name="seo_content_briefs")
    op.drop_index("ix_seo_content_briefs_prompt_version", table_name="seo_content_briefs")
    op.drop_index("ix_seo_content_briefs_status", table_name="seo_content_briefs")
    op.drop_index("ix_seo_content_briefs_brief_type", table_name="seo_content_briefs")
    op.drop_index("ix_seo_content_briefs_recommendation_id", table_name="seo_content_briefs")
    op.drop_index("ix_seo_content_briefs_organization_id", table_name="seo_content_briefs")
    op.drop_table("seo_content_briefs")
