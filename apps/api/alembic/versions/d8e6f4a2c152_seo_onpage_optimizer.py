"""SEO on-page optimizer (M9.10).

Revision ID: d8e6f4a2c152
Revises: c7f5a3b2d041
Create Date: 2026-09-27
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "d8e6f4a2c152"
down_revision: Union[str, None] = "c7f5a3b2d041"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "seo_onpage_optimization_runs",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.Column("generated_content_id", sa.UUID(), nullable=False),
        sa.Column("content_brief_id", sa.UUID(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="completed"),
        sa.Column("analysis_key", sa.String(length=64), nullable=False),
        sa.Column("stats", sa.JSON(), nullable=False),
        sa.Column("limitations", sa.JSON(), nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=False, server_default="none"),
        sa.Column("model", sa.String(length=64), nullable=False, server_default="none"),
        sa.Column("prompt_version", sa.String(length=32), nullable=False, server_default="seo_onpage_optimizer_prompt_v1"),
        sa.Column("algorithm_version", sa.String(length=32), nullable=False, server_default="seo_onpage_optimizer_v1"),
        sa.Column("ai_enriched", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["generated_content_id"], ["seo_generated_content.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["content_brief_id"], ["seo_content_briefs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("organization_id", "analysis_key", name="uq_seo_onpage_runs_org_analysis_key"),
    )
    op.create_index("ix_seo_onpage_optimization_runs_organization_id", "seo_onpage_optimization_runs", ["organization_id"])
    op.create_index(
        "ix_seo_onpage_optimization_runs_generated_content_id",
        "seo_onpage_optimization_runs",
        ["generated_content_id"],
    )
    op.create_index(
        "ix_seo_onpage_optimization_runs_content_brief_id", "seo_onpage_optimization_runs", ["content_brief_id"]
    )
    op.create_index("ix_seo_onpage_optimization_runs_status", "seo_onpage_optimization_runs", ["status"])
    op.create_index("ix_seo_onpage_optimization_runs_analysis_key", "seo_onpage_optimization_runs", ["analysis_key"])
    op.create_index(
        "ix_seo_onpage_optimization_runs_algorithm_version", "seo_onpage_optimization_runs", ["algorithm_version"]
    )

    op.create_table(
        "seo_onpage_findings",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.Column("run_id", sa.UUID(), nullable=False),
        sa.Column("generated_content_id", sa.UUID(), nullable=False),
        sa.Column("content_brief_id", sa.UUID(), nullable=False),
        sa.Column("finding_type", sa.String(length=64), nullable=False),
        sa.Column("category", sa.String(length=32), nullable=False),
        sa.Column("severity", sa.String(length=16), nullable=False),
        sa.Column("priority", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="open"),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("rationale", sa.Text(), nullable=False),
        sa.Column("current_value", sa.Text(), nullable=True),
        sa.Column("expected_value", sa.Text(), nullable=True),
        sa.Column("recommendation", sa.Text(), nullable=False),
        sa.Column("evidence_refs", sa.JSON(), nullable=False),
        sa.Column("affected_section", sa.String(length=255), nullable=True),
        sa.Column("affected_element", sa.String(length=64), nullable=True),
        sa.Column("suggested_change", sa.Text(), nullable=True),
        sa.Column("dedupe_key", sa.String(length=128), nullable=False),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["run_id"], ["seo_onpage_optimization_runs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["generated_content_id"], ["seo_generated_content.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["content_brief_id"], ["seo_content_briefs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("organization_id", "run_id", "dedupe_key", name="uq_seo_onpage_findings_run_dedupe"),
    )
    op.create_index("ix_seo_onpage_findings_organization_id", "seo_onpage_findings", ["organization_id"])
    op.create_index("ix_seo_onpage_findings_run_id", "seo_onpage_findings", ["run_id"])
    op.create_index("ix_seo_onpage_findings_generated_content_id", "seo_onpage_findings", ["generated_content_id"])
    op.create_index("ix_seo_onpage_findings_content_brief_id", "seo_onpage_findings", ["content_brief_id"])
    op.create_index("ix_seo_onpage_findings_finding_type", "seo_onpage_findings", ["finding_type"])
    op.create_index("ix_seo_onpage_findings_category", "seo_onpage_findings", ["category"])
    op.create_index("ix_seo_onpage_findings_severity", "seo_onpage_findings", ["severity"])
    op.create_index("ix_seo_onpage_findings_priority", "seo_onpage_findings", ["priority"])
    op.create_index("ix_seo_onpage_findings_status", "seo_onpage_findings", ["status"])
    op.create_index("ix_seo_onpage_findings_dedupe_key", "seo_onpage_findings", ["dedupe_key"])


def downgrade() -> None:
    op.drop_index("ix_seo_onpage_findings_dedupe_key", table_name="seo_onpage_findings")
    op.drop_index("ix_seo_onpage_findings_status", table_name="seo_onpage_findings")
    op.drop_index("ix_seo_onpage_findings_priority", table_name="seo_onpage_findings")
    op.drop_index("ix_seo_onpage_findings_severity", table_name="seo_onpage_findings")
    op.drop_index("ix_seo_onpage_findings_category", table_name="seo_onpage_findings")
    op.drop_index("ix_seo_onpage_findings_finding_type", table_name="seo_onpage_findings")
    op.drop_index("ix_seo_onpage_findings_content_brief_id", table_name="seo_onpage_findings")
    op.drop_index("ix_seo_onpage_findings_generated_content_id", table_name="seo_onpage_findings")
    op.drop_index("ix_seo_onpage_findings_run_id", table_name="seo_onpage_findings")
    op.drop_index("ix_seo_onpage_findings_organization_id", table_name="seo_onpage_findings")
    op.drop_table("seo_onpage_findings")

    op.drop_index("ix_seo_onpage_optimization_runs_algorithm_version", table_name="seo_onpage_optimization_runs")
    op.drop_index("ix_seo_onpage_optimization_runs_analysis_key", table_name="seo_onpage_optimization_runs")
    op.drop_index("ix_seo_onpage_optimization_runs_status", table_name="seo_onpage_optimization_runs")
    op.drop_index("ix_seo_onpage_optimization_runs_content_brief_id", table_name="seo_onpage_optimization_runs")
    op.drop_index("ix_seo_onpage_optimization_runs_generated_content_id", table_name="seo_onpage_optimization_runs")
    op.drop_index("ix_seo_onpage_optimization_runs_organization_id", table_name="seo_onpage_optimization_runs")
    op.drop_table("seo_onpage_optimization_runs")
