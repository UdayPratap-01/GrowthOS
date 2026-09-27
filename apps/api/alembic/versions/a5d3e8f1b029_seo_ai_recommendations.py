"""SEO AI recommendations (M9.7).

Revision ID: a5d3e8f1b029
Revises: f4c9d2e8a017
Create Date: 2026-09-27
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "a5d3e8f1b029"
down_revision: Union[str, None] = "f4c9d2e8a017"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "seo_recommendation_runs",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.Column("sync_id", sa.UUID(), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="completed"),
        sa.Column("generation_key", sa.String(length=64), nullable=False),
        sa.Column("evidence_snapshot", sa.JSON(), nullable=False),
        sa.Column("stats", sa.JSON(), nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=False, server_default="unknown"),
        sa.Column("model", sa.String(length=64), nullable=False, server_default="unknown"),
        sa.Column("prompt_version", sa.String(length=32), nullable=False, server_default="seo_recommendation_prompt_v1"),
        sa.Column("algorithm_version", sa.String(length=32), nullable=False, server_default="seo_recommendation_v1"),
        sa.Column("error", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["sync_id"], ["search_console_syncs.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("organization_id", "generation_key", name="uq_seo_recommendation_runs_org_gen_key"),
    )
    op.create_index("ix_seo_recommendation_runs_organization_id", "seo_recommendation_runs", ["organization_id"])
    op.create_index("ix_seo_recommendation_runs_sync_id", "seo_recommendation_runs", ["sync_id"])
    op.create_index("ix_seo_recommendation_runs_status", "seo_recommendation_runs", ["status"])
    op.create_index("ix_seo_recommendation_runs_generation_key", "seo_recommendation_runs", ["generation_key"])
    op.create_index("ix_seo_recommendation_runs_algorithm_version", "seo_recommendation_runs", ["algorithm_version"])

    op.create_table(
        "seo_recommendations",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.Column("run_id", sa.UUID(), nullable=False),
        sa.Column("sync_id", sa.UUID(), nullable=True),
        sa.Column("recommendation_type", sa.String(length=64), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("rationale", sa.Text(), nullable=False),
        sa.Column("priority", sa.String(length=16), nullable=False),
        sa.Column("impact", sa.String(length=16), nullable=False),
        sa.Column("effort", sa.String(length=16), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False, server_default="0"),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="open"),
        sa.Column("evidence_refs", sa.JSON(), nullable=False),
        sa.Column("affected_urls", sa.JSON(), nullable=False),
        sa.Column("affected_keywords", sa.JSON(), nullable=False),
        sa.Column("affected_topics", sa.JSON(), nullable=False),
        sa.Column("competitor_context", sa.JSON(), nullable=False),
        sa.Column("recommended_action", sa.Text(), nullable=False),
        sa.Column("expected_outcome", sa.Text(), nullable=False),
        sa.Column("limitations", sa.JSON(), nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("model", sa.String(length=64), nullable=False),
        sa.Column("prompt_version", sa.String(length=32), nullable=False),
        sa.Column("algorithm_version", sa.String(length=32), nullable=False),
        sa.Column("generation_key", sa.String(length=64), nullable=False),
        sa.Column("dedupe_key", sa.String(length=128), nullable=False),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["run_id"], ["seo_recommendation_runs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["sync_id"], ["search_console_syncs.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("organization_id", "dedupe_key", name="uq_seo_recommendations_org_dedupe"),
    )
    op.create_index("ix_seo_recommendations_organization_id", "seo_recommendations", ["organization_id"])
    op.create_index("ix_seo_recommendations_run_id", "seo_recommendations", ["run_id"])
    op.create_index("ix_seo_recommendations_sync_id", "seo_recommendations", ["sync_id"])
    op.create_index("ix_seo_recommendations_recommendation_type", "seo_recommendations", ["recommendation_type"])
    op.create_index("ix_seo_recommendations_priority", "seo_recommendations", ["priority"])
    op.create_index("ix_seo_recommendations_status", "seo_recommendations", ["status"])
    op.create_index("ix_seo_recommendations_prompt_version", "seo_recommendations", ["prompt_version"])
    op.create_index("ix_seo_recommendations_algorithm_version", "seo_recommendations", ["algorithm_version"])
    op.create_index("ix_seo_recommendations_generation_key", "seo_recommendations", ["generation_key"])


def downgrade() -> None:
    op.drop_index("ix_seo_recommendations_generation_key", table_name="seo_recommendations")
    op.drop_index("ix_seo_recommendations_algorithm_version", table_name="seo_recommendations")
    op.drop_index("ix_seo_recommendations_prompt_version", table_name="seo_recommendations")
    op.drop_index("ix_seo_recommendations_status", table_name="seo_recommendations")
    op.drop_index("ix_seo_recommendations_priority", table_name="seo_recommendations")
    op.drop_index("ix_seo_recommendations_recommendation_type", table_name="seo_recommendations")
    op.drop_index("ix_seo_recommendations_sync_id", table_name="seo_recommendations")
    op.drop_index("ix_seo_recommendations_run_id", table_name="seo_recommendations")
    op.drop_index("ix_seo_recommendations_organization_id", table_name="seo_recommendations")
    op.drop_table("seo_recommendations")
    op.drop_index("ix_seo_recommendation_runs_algorithm_version", table_name="seo_recommendation_runs")
    op.drop_index("ix_seo_recommendation_runs_generation_key", table_name="seo_recommendation_runs")
    op.drop_index("ix_seo_recommendation_runs_status", table_name="seo_recommendation_runs")
    op.drop_index("ix_seo_recommendation_runs_sync_id", table_name="seo_recommendation_runs")
    op.drop_index("ix_seo_recommendation_runs_organization_id", table_name="seo_recommendation_runs")
    op.drop_table("seo_recommendation_runs")
