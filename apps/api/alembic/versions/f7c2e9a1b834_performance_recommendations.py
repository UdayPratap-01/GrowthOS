"""Performance recommendations — analysis-only intelligence layer.

Revision ID: f7c2e9a1b834
Revises: e5b9c1d4a702
Create Date: 2026-08-27
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "f7c2e9a1b834"
down_revision: Union[str, None] = "e5b9c1d4a702"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "performance_recommendations",
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
        sa.Column("recommendation_type", sa.String(length=64), nullable=False),
        sa.Column("severity", sa.String(length=16), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("explanation", sa.Text(), nullable=False),
        sa.Column("evidence", sa.JSON(), nullable=False),
        sa.Column("affected_metrics", sa.JSON(), nullable=False),
        sa.Column("current_values", sa.JSON(), nullable=False),
        sa.Column("comparison_values", sa.JSON(), nullable=False),
        sa.Column("percentage_changes", sa.JSON(), nullable=False),
        sa.Column("confidence", sa.Numeric(precision=5, scale=4), nullable=False),
        sa.Column("suggested_action", sa.JSON(), nullable=False),
        sa.Column("signal_category", sa.String(length=32), nullable=False),
        sa.Column("analysis_window_days", sa.Integer(), nullable=False),
        sa.Column("window_current_start", sa.Date(), nullable=False),
        sa.Column("window_current_end", sa.Date(), nullable=False),
        sa.Column("window_previous_start", sa.Date(), nullable=False),
        sa.Column("window_previous_end", sa.Date(), nullable=False),
        sa.Column("fingerprint", sa.String(length=128), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "NEW",
                "REVIEWED",
                "APPROVED",
                "REJECTED",
                "EXPIRED",
                name="performance_recommendation_status",
                native_enum=False,
            ),
            nullable=False,
        ),
        sa.Column("explanation_source", sa.String(length=32), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["client_id"], ["clients.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("organization_id", "fingerprint", name="uq_perf_recommendation_org_fingerprint"),
    )
    op.create_index("ix_performance_recommendations_organization_id", "performance_recommendations", ["organization_id"])
    op.create_index("ix_performance_recommendations_client_id", "performance_recommendations", ["client_id"])
    op.create_index("ix_performance_recommendations_platform", "performance_recommendations", ["platform"])
    op.create_index("ix_performance_recommendations_recommendation_type", "performance_recommendations", ["recommendation_type"])
    op.create_index("ix_performance_recommendations_status", "performance_recommendations", ["status"])
    op.create_index("ix_perf_rec_org_status", "performance_recommendations", ["organization_id", "status"])
    op.create_index("ix_perf_rec_org_platform", "performance_recommendations", ["organization_id", "platform"])
    op.create_index("ix_perf_rec_org_client", "performance_recommendations", ["organization_id", "client_id"])
    op.create_index("ix_perf_rec_org_created", "performance_recommendations", ["organization_id", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_perf_rec_org_created", table_name="performance_recommendations")
    op.drop_index("ix_perf_rec_org_client", table_name="performance_recommendations")
    op.drop_index("ix_perf_rec_org_platform", table_name="performance_recommendations")
    op.drop_index("ix_perf_rec_org_status", table_name="performance_recommendations")
    op.drop_index("ix_performance_recommendations_status", table_name="performance_recommendations")
    op.drop_index("ix_performance_recommendations_recommendation_type", table_name="performance_recommendations")
    op.drop_index("ix_performance_recommendations_platform", table_name="performance_recommendations")
    op.drop_index("ix_performance_recommendations_client_id", table_name="performance_recommendations")
    op.drop_index("ix_performance_recommendations_organization_id", table_name="performance_recommendations")
    op.drop_table("performance_recommendations")
