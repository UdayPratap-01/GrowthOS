"""SEO topic clustering engine (M9.5).

Revision ID: e2b6c4d9f018
Revises: d1a5f3c8e947
Create Date: 2026-09-27
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "e2b6c4d9f018"
down_revision: Union[str, None] = "d1a5f3c8e947"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "topic_clusters",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("sync_id", sa.UUID(), nullable=False),
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.Column("site_url", sa.String(length=2048), nullable=False),
        sa.Column("cluster_key", sa.String(length=64), nullable=False),
        sa.Column("topic_label", sa.String(length=512), nullable=False),
        sa.Column("representative_query", sa.Text(), nullable=False),
        sa.Column("query_count", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("page_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_clicks", sa.Float(), nullable=False, server_default="0"),
        sa.Column("total_impressions", sa.Float(), nullable=False, server_default="0"),
        sa.Column("aggregate_ctr", sa.Float(), nullable=False, server_default="0"),
        sa.Column("weighted_average_position", sa.Float(), nullable=False, server_default="0"),
        sa.Column("opportunity_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("multi_page_signal", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("is_singleton", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("algorithm_version", sa.String(length=16), nullable=False, server_default="v1"),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="active"),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["sync_id"], ["search_console_syncs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("sync_id", "cluster_key", name="uq_topic_clusters_sync_cluster_key"),
    )
    op.create_index("ix_topic_clusters_sync_id", "topic_clusters", ["sync_id"])
    op.create_index("ix_topic_clusters_organization_id", "topic_clusters", ["organization_id"])
    op.create_index("ix_topic_clusters_site_url", "topic_clusters", ["site_url"])
    op.create_index("ix_topic_clusters_cluster_key", "topic_clusters", ["cluster_key"])
    op.create_index("ix_topic_clusters_algorithm_version", "topic_clusters", ["algorithm_version"])
    op.create_index("ix_topic_clusters_status", "topic_clusters", ["status"])

    op.create_table(
        "topic_cluster_queries",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("topic_cluster_id", sa.UUID(), nullable=False),
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.Column("query", sa.Text(), nullable=False),
        sa.Column("normalized_query", sa.String(length=1024), nullable=False),
        sa.Column("similarity_score", sa.Float(), nullable=False, server_default="0"),
        sa.Column("membership_reason", sa.String(length=64), nullable=False, server_default="jaccard_token_overlap"),
        sa.Column("clicks", sa.Float(), nullable=False, server_default="0"),
        sa.Column("impressions", sa.Float(), nullable=False, server_default="0"),
        sa.Column("ctr", sa.Float(), nullable=False, server_default="0"),
        sa.Column("average_position", sa.Float(), nullable=False, server_default="0"),
        sa.Column("opportunity_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("is_representative", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["topic_cluster_id"], ["topic_clusters.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("topic_cluster_id", "normalized_query", name="uq_topic_cluster_queries_cluster_norm"),
    )
    op.create_index("ix_topic_cluster_queries_topic_cluster_id", "topic_cluster_queries", ["topic_cluster_id"])
    op.create_index("ix_topic_cluster_queries_organization_id", "topic_cluster_queries", ["organization_id"])
    op.create_index("ix_topic_cluster_queries_normalized_query", "topic_cluster_queries", ["normalized_query"])

    op.create_table(
        "topic_cluster_pages",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("topic_cluster_id", sa.UUID(), nullable=False),
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.Column("page_url", sa.String(length=2048), nullable=False),
        sa.Column("clicks", sa.Float(), nullable=False, server_default="0"),
        sa.Column("impressions", sa.Float(), nullable=False, server_default="0"),
        sa.Column("ctr", sa.Float(), nullable=False, server_default="0"),
        sa.Column("average_position", sa.Float(), nullable=False, server_default="0"),
        sa.Column("is_primary", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["topic_cluster_id"], ["topic_clusters.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("topic_cluster_id", "page_url", name="uq_topic_cluster_pages_cluster_url"),
    )
    op.create_index("ix_topic_cluster_pages_topic_cluster_id", "topic_cluster_pages", ["topic_cluster_id"])
    op.create_index("ix_topic_cluster_pages_organization_id", "topic_cluster_pages", ["organization_id"])
    op.create_index("ix_topic_cluster_pages_page_url", "topic_cluster_pages", ["page_url"])


def downgrade() -> None:
    op.drop_index("ix_topic_cluster_pages_page_url", table_name="topic_cluster_pages")
    op.drop_index("ix_topic_cluster_pages_organization_id", table_name="topic_cluster_pages")
    op.drop_index("ix_topic_cluster_pages_topic_cluster_id", table_name="topic_cluster_pages")
    op.drop_table("topic_cluster_pages")
    op.drop_index("ix_topic_cluster_queries_normalized_query", table_name="topic_cluster_queries")
    op.drop_index("ix_topic_cluster_queries_organization_id", table_name="topic_cluster_queries")
    op.drop_index("ix_topic_cluster_queries_topic_cluster_id", table_name="topic_cluster_queries")
    op.drop_table("topic_cluster_queries")
    op.drop_index("ix_topic_clusters_status", table_name="topic_clusters")
    op.drop_index("ix_topic_clusters_algorithm_version", table_name="topic_clusters")
    op.drop_index("ix_topic_clusters_cluster_key", table_name="topic_clusters")
    op.drop_index("ix_topic_clusters_site_url", table_name="topic_clusters")
    op.drop_index("ix_topic_clusters_organization_id", table_name="topic_clusters")
    op.drop_index("ix_topic_clusters_sync_id", table_name="topic_clusters")
    op.drop_table("topic_clusters")
