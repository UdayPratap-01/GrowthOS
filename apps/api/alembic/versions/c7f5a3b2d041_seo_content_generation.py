"""SEO AI content generation (M9.9).

Revision ID: c7f5a3b2d041
Revises: b6e4f2a9c130
Create Date: 2026-09-27
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "c7f5a3b2d041"
down_revision: Union[str, None] = "b6e4f2a9c130"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "seo_generated_content",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.Column("content_brief_id", sa.UUID(), nullable=False),
        sa.Column("recommendation_id", sa.UUID(), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("slug", sa.String(length=128), nullable=False),
        sa.Column("content_type", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="draft"),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("structured_sections", sa.JSON(), nullable=False),
        sa.Column("primary_keyword", sa.String(length=255), nullable=False),
        sa.Column("secondary_keywords", sa.JSON(), nullable=False),
        sa.Column("target_topic", sa.String(length=512), nullable=True),
        sa.Column("target_url", sa.String(length=2048), nullable=True),
        sa.Column("meta_title", sa.String(length=70), nullable=False),
        sa.Column("meta_description", sa.String(length=160), nullable=False),
        sa.Column("outline_used", sa.JSON(), nullable=False),
        sa.Column("internal_link_targets", sa.JSON(), nullable=False),
        sa.Column("evidence_refs", sa.JSON(), nullable=False),
        sa.Column("limitations", sa.JSON(), nullable=False),
        sa.Column("brief_snapshot", sa.JSON(), nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("model", sa.String(length=64), nullable=False),
        sa.Column("prompt_version", sa.String(length=32), nullable=False, server_default="seo_content_generation_prompt_v1"),
        sa.Column("algorithm_version", sa.String(length=32), nullable=False, server_default="seo_content_generation_v1"),
        sa.Column("generation_key", sa.String(length=64), nullable=False),
        sa.Column("word_count", sa.Integer(), nullable=False, server_default="0"),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["content_brief_id"], ["seo_content_briefs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["recommendation_id"], ["seo_recommendations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("organization_id", "generation_key", name="uq_seo_generated_content_org_gen_key"),
    )
    op.create_index("ix_seo_generated_content_organization_id", "seo_generated_content", ["organization_id"])
    op.create_index("ix_seo_generated_content_content_brief_id", "seo_generated_content", ["content_brief_id"])
    op.create_index("ix_seo_generated_content_recommendation_id", "seo_generated_content", ["recommendation_id"])
    op.create_index("ix_seo_generated_content_slug", "seo_generated_content", ["slug"])
    op.create_index("ix_seo_generated_content_content_type", "seo_generated_content", ["content_type"])
    op.create_index("ix_seo_generated_content_status", "seo_generated_content", ["status"])
    op.create_index("ix_seo_generated_content_prompt_version", "seo_generated_content", ["prompt_version"])
    op.create_index("ix_seo_generated_content_algorithm_version", "seo_generated_content", ["algorithm_version"])
    op.create_index("ix_seo_generated_content_generation_key", "seo_generated_content", ["generation_key"])


def downgrade() -> None:
    op.drop_index("ix_seo_generated_content_generation_key", table_name="seo_generated_content")
    op.drop_index("ix_seo_generated_content_algorithm_version", table_name="seo_generated_content")
    op.drop_index("ix_seo_generated_content_prompt_version", table_name="seo_generated_content")
    op.drop_index("ix_seo_generated_content_status", table_name="seo_generated_content")
    op.drop_index("ix_seo_generated_content_content_type", table_name="seo_generated_content")
    op.drop_index("ix_seo_generated_content_slug", table_name="seo_generated_content")
    op.drop_index("ix_seo_generated_content_recommendation_id", table_name="seo_generated_content")
    op.drop_index("ix_seo_generated_content_content_brief_id", table_name="seo_generated_content")
    op.drop_index("ix_seo_generated_content_organization_id", table_name="seo_generated_content")
    op.drop_table("seo_generated_content")
