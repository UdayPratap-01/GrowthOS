"""SEO schema generator/validator (M9.11).

Revision ID: e9f7a5b3d263
Revises: d8e6f4a2c152
Create Date: 2026-09-27
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "e9f7a5b3d263"
down_revision: Union[str, None] = "d8e6f4a2c152"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "seo_schema_artifacts",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.Column("generated_content_id", sa.UUID(), nullable=False),
        sa.Column("content_brief_id", sa.UUID(), nullable=False),
        sa.Column("schema_type", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="draft"),
        sa.Column("json_ld", sa.JSON(), nullable=True),
        sa.Column("validation_status", sa.String(length=16), nullable=False, server_default="not_generated"),
        sa.Column("validation_errors", sa.JSON(), nullable=False),
        sa.Column("validation_warnings", sa.JSON(), nullable=False),
        sa.Column("eligibility_status", sa.String(length=16), nullable=False),
        sa.Column("eligibility_reasons", sa.JSON(), nullable=False),
        sa.Column("evidence_refs", sa.JSON(), nullable=False),
        sa.Column("source_fields", sa.JSON(), nullable=False),
        sa.Column("limitations", sa.JSON(), nullable=False),
        sa.Column("generation_key", sa.String(length=64), nullable=False),
        sa.Column("generation_algorithm_version", sa.String(length=32), nullable=False, server_default="seo_schema_generation_v1"),
        sa.Column("validation_algorithm_version", sa.String(length=32), nullable=False, server_default="seo_schema_validation_v1"),
        sa.Column("prompt_version", sa.String(length=32), nullable=False, server_default="seo_schema_prompt_v1"),
        sa.Column("provider", sa.String(length=32), nullable=False, server_default="none"),
        sa.Column("model", sa.String(length=64), nullable=False, server_default="none"),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["generated_content_id"], ["seo_generated_content.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["content_brief_id"], ["seo_content_briefs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("organization_id", "generation_key", name="uq_seo_schema_artifacts_org_gen_key"),
    )
    op.create_index("ix_seo_schema_artifacts_organization_id", "seo_schema_artifacts", ["organization_id"])
    op.create_index("ix_seo_schema_artifacts_generated_content_id", "seo_schema_artifacts", ["generated_content_id"])
    op.create_index("ix_seo_schema_artifacts_content_brief_id", "seo_schema_artifacts", ["content_brief_id"])
    op.create_index("ix_seo_schema_artifacts_schema_type", "seo_schema_artifacts", ["schema_type"])
    op.create_index("ix_seo_schema_artifacts_status", "seo_schema_artifacts", ["status"])
    op.create_index("ix_seo_schema_artifacts_validation_status", "seo_schema_artifacts", ["validation_status"])
    op.create_index("ix_seo_schema_artifacts_eligibility_status", "seo_schema_artifacts", ["eligibility_status"])
    op.create_index("ix_seo_schema_artifacts_generation_key", "seo_schema_artifacts", ["generation_key"])
    op.create_index(
        "ix_seo_schema_artifacts_generation_algorithm_version",
        "seo_schema_artifacts",
        ["generation_algorithm_version"],
    )
    op.create_index(
        "ix_seo_schema_artifacts_validation_algorithm_version",
        "seo_schema_artifacts",
        ["validation_algorithm_version"],
    )

    op.create_table(
        "seo_schema_findings",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.Column("artifact_id", sa.UUID(), nullable=False),
        sa.Column("generated_content_id", sa.UUID(), nullable=False),
        sa.Column("finding_type", sa.String(length=64), nullable=False),
        sa.Column("category", sa.String(length=32), nullable=False),
        sa.Column("severity", sa.String(length=16), nullable=False),
        sa.Column("level", sa.String(length=16), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("rationale", sa.Text(), nullable=False),
        sa.Column("property_path", sa.String(length=255), nullable=True),
        sa.Column("evidence_refs", sa.JSON(), nullable=False),
        sa.Column("dedupe_key", sa.String(length=128), nullable=False),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["artifact_id"], ["seo_schema_artifacts.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["generated_content_id"], ["seo_generated_content.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("organization_id", "artifact_id", "dedupe_key", name="uq_seo_schema_findings_artifact_dedupe"),
    )
    op.create_index("ix_seo_schema_findings_organization_id", "seo_schema_findings", ["organization_id"])
    op.create_index("ix_seo_schema_findings_artifact_id", "seo_schema_findings", ["artifact_id"])
    op.create_index("ix_seo_schema_findings_generated_content_id", "seo_schema_findings", ["generated_content_id"])
    op.create_index("ix_seo_schema_findings_finding_type", "seo_schema_findings", ["finding_type"])
    op.create_index("ix_seo_schema_findings_category", "seo_schema_findings", ["category"])
    op.create_index("ix_seo_schema_findings_severity", "seo_schema_findings", ["severity"])
    op.create_index("ix_seo_schema_findings_level", "seo_schema_findings", ["level"])
    op.create_index("ix_seo_schema_findings_dedupe_key", "seo_schema_findings", ["dedupe_key"])


def downgrade() -> None:
    op.drop_index("ix_seo_schema_findings_dedupe_key", table_name="seo_schema_findings")
    op.drop_index("ix_seo_schema_findings_level", table_name="seo_schema_findings")
    op.drop_index("ix_seo_schema_findings_severity", table_name="seo_schema_findings")
    op.drop_index("ix_seo_schema_findings_category", table_name="seo_schema_findings")
    op.drop_index("ix_seo_schema_findings_finding_type", table_name="seo_schema_findings")
    op.drop_index("ix_seo_schema_findings_generated_content_id", table_name="seo_schema_findings")
    op.drop_index("ix_seo_schema_findings_artifact_id", table_name="seo_schema_findings")
    op.drop_index("ix_seo_schema_findings_organization_id", table_name="seo_schema_findings")
    op.drop_table("seo_schema_findings")

    op.drop_index("ix_seo_schema_artifacts_validation_algorithm_version", table_name="seo_schema_artifacts")
    op.drop_index("ix_seo_schema_artifacts_generation_algorithm_version", table_name="seo_schema_artifacts")
    op.drop_index("ix_seo_schema_artifacts_generation_key", table_name="seo_schema_artifacts")
    op.drop_index("ix_seo_schema_artifacts_eligibility_status", table_name="seo_schema_artifacts")
    op.drop_index("ix_seo_schema_artifacts_validation_status", table_name="seo_schema_artifacts")
    op.drop_index("ix_seo_schema_artifacts_status", table_name="seo_schema_artifacts")
    op.drop_index("ix_seo_schema_artifacts_schema_type", table_name="seo_schema_artifacts")
    op.drop_index("ix_seo_schema_artifacts_content_brief_id", table_name="seo_schema_artifacts")
    op.drop_index("ix_seo_schema_artifacts_generated_content_id", table_name="seo_schema_artifacts")
    op.drop_index("ix_seo_schema_artifacts_organization_id", table_name="seo_schema_artifacts")
    op.drop_table("seo_schema_artifacts")
