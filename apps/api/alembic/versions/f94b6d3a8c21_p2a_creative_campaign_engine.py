"""P2-A: campaign briefs, creative concepts, variations, generation runs, approval lifecycle

Revision ID: f94b6d3a8c21
Revises: e83a5c2d7b16
Create Date: 2026-08-11

Statuses are stored as strings, not native PostgreSQL enums, for the same reason
the billing migration did: a lifecycle gains states, and adding one to a native
enum needs DDL on a live table.

`campaigns.review_status` is added alongside the existing `status` rather than
replacing it. `status` describes what an ad platform is doing with a campaign
(active, paused); `review_status` describes what a human has agreed to inside
GrowthOS. Collapsing them would mean losing the approval record as soon as a
campaign went live, and would make "approved" indistinguishable from "delivering".

No PUBLISHED state exists anywhere here. `campaigns.external_id` is the only
evidence a campaign exists on a platform, and nothing in P2-A writes it.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "f94b6d3a8c21"
down_revision: Union[str, None] = "e83a5c2d7b16"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

#: JSON collection columns are NOT NULL with an empty default, matching the
#: models, which type them as `Mapped[dict]` / `Mapped[list]` rather than
#: `Optional`. A nullable column here would let a None reach code that iterates
#: the value, and would leave `alembic check` reporting permanent drift.
EMPTY_DICT = sa.text("'{}'")
EMPTY_LIST = sa.text("'[]'")


def upgrade() -> None:
    # ------------------------------------------------------------------
    # campaign_briefs
    # ------------------------------------------------------------------
    op.create_table(
        "campaign_briefs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "organization_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "client_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("clients.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("campaign_name", sa.String(length=255), nullable=False),
        sa.Column("platform", sa.String(length=64), nullable=False, server_default="meta"),
        sa.Column("objective", sa.String(length=64), nullable=False, server_default="lead_generation"),
        sa.Column("offer", sa.Text(), nullable=True),
        sa.Column("audience", sa.Text(), nullable=True),
        sa.Column("pain_points", sa.JSON(), nullable=False, server_default=EMPTY_LIST),
        sa.Column("value_proposition", sa.Text(), nullable=True),
        sa.Column("messaging_angle", sa.Text(), nullable=True),
        sa.Column("tone", sa.String(length=120), nullable=True),
        sa.Column("brand_constraints", sa.JSON(), nullable=False, server_default=EMPTY_LIST),
        sa.Column("total_budget", sa.Numeric(precision=12, scale=2), nullable=True),
        sa.Column("daily_budget", sa.Numeric(precision=12, scale=2), nullable=True),
        sa.Column("monthly_budget", sa.Numeric(precision=12, scale=2), nullable=True),
        sa.Column("currency", sa.String(length=8), nullable=False, server_default="USD"),
        sa.Column("success_metrics", sa.JSON(), nullable=False, server_default=EMPTY_LIST),
        sa.Column("creative_direction", sa.Text(), nullable=True),
        sa.Column("cta", sa.String(length=120), nullable=True),
        sa.Column("data_limitations", sa.JSON(), nullable=False, server_default=EMPTY_LIST),
        sa.Column("strategy", sa.JSON(), nullable=False, server_default=EMPTY_DICT),
        sa.Column("client_context_snapshot", sa.JSON(), nullable=False, server_default=EMPTY_DICT),
        sa.Column("data_source", sa.String(length=32), nullable=False, server_default="live"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_campaign_briefs_organization_id", "campaign_briefs", ["organization_id"])
    op.create_index("ix_campaign_briefs_client_id", "campaign_briefs", ["client_id"])

    # ------------------------------------------------------------------
    # campaign_generation_runs
    # ------------------------------------------------------------------
    op.create_table(
        "campaign_generation_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "organization_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "client_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("clients.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "brief_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("campaign_briefs.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "campaign_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("campaigns.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "requested_by",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="QUEUED"),
        sa.Column("platform", sa.String(length=64), nullable=False, server_default="meta"),
        sa.Column("objective", sa.String(length=64), nullable=False, server_default="lead_generation"),
        sa.Column("request", sa.JSON(), nullable=False, server_default=EMPTY_DICT),
        sa.Column("stages", sa.JSON(), nullable=False, server_default=EMPTY_LIST),
        sa.Column("result", sa.JSON(), nullable=False, server_default=EMPTY_DICT),
        sa.Column("data_limitations", sa.JSON(), nullable=False, server_default=EMPTY_LIST),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("error_code", sa.String(length=120), nullable=True),
        sa.Column(
            "background_job_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("background_jobs.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("idempotency_key", sa.String(length=255), nullable=True),
        sa.Column("image_quantity", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("video_quantity", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("concept_quantity", sa.Integer(), nullable=False, server_default="3"),
        sa.Column("variation_quantity", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("demo_mode", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint(
            "organization_id", "idempotency_key", name="uq_generation_run_idempotency"
        ),
    )
    op.create_index(
        "ix_campaign_generation_runs_organization_id",
        "campaign_generation_runs",
        ["organization_id"],
    )
    op.create_index(
        "ix_campaign_generation_runs_client_id", "campaign_generation_runs", ["client_id"]
    )
    op.create_index("ix_campaign_generation_runs_status", "campaign_generation_runs", ["status"])
    op.create_index(
        "ix_campaign_generation_runs_org_client",
        "campaign_generation_runs",
        ["organization_id", "client_id"],
    )

    # ------------------------------------------------------------------
    # creative_concepts
    # ------------------------------------------------------------------
    op.create_table(
        "creative_concepts",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "organization_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "client_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("clients.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "brief_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("campaign_briefs.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column(
            "campaign_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("campaigns.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "run_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("campaign_generation_runs.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("reference", sa.String(length=16), nullable=False, server_default="A"),
        sa.Column("angle", sa.Text(), nullable=False),
        sa.Column("hook", sa.Text(), nullable=True),
        sa.Column("primary_text", sa.Text(), nullable=True),
        sa.Column("headline", sa.String(length=512), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("cta", sa.String(length=120), nullable=True),
        sa.Column("tone", sa.String(length=120), nullable=True),
        sa.Column("audience", sa.Text(), nullable=True),
        sa.Column("objective", sa.String(length=64), nullable=True),
        sa.Column("platform", sa.String(length=64), nullable=True),
        sa.Column("visual_direction", sa.JSON(), nullable=False, server_default=EMPTY_DICT),
        sa.Column("image_prompt", sa.Text(), nullable=True),
        sa.Column("video_prompt", sa.Text(), nullable=True),
        sa.Column("negative_constraints", sa.JSON(), nullable=False, server_default=EMPTY_LIST),
        sa.Column("aspect_ratios", sa.JSON(), nullable=False, server_default=EMPTY_LIST),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="DRAFT"),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("data_limitations", sa.JSON(), nullable=False, server_default=EMPTY_LIST),
        sa.Column("meta", sa.JSON(), nullable=False, server_default=EMPTY_DICT),
        sa.Column("data_source", sa.String(length=32), nullable=False, server_default="live"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_creative_concepts_organization_id", "creative_concepts", ["organization_id"])
    op.create_index("ix_creative_concepts_client_id", "creative_concepts", ["client_id"])
    op.create_index("ix_creative_concepts_brief_id", "creative_concepts", ["brief_id"])
    op.create_index("ix_creative_concepts_campaign_id", "creative_concepts", ["campaign_id"])
    op.create_index("ix_creative_concepts_status", "creative_concepts", ["status"])
    op.create_index(
        "ix_creative_concepts_org_campaign",
        "creative_concepts",
        ["organization_id", "campaign_id"],
    )

    # ------------------------------------------------------------------
    # creative_variations
    # ------------------------------------------------------------------
    op.create_table(
        "creative_variations",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "organization_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "client_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("clients.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "parent_concept_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("creative_concepts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "campaign_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("campaigns.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "run_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("campaign_generation_runs.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("reference", sa.String(length=16), nullable=False, server_default="B"),
        sa.Column("axis", sa.String(length=32), nullable=False, server_default="hook"),
        sa.Column("hypothesis", sa.Text(), nullable=False),
        sa.Column("creative_type", sa.String(length=32), nullable=False, server_default="copy"),
        sa.Column("hook", sa.Text(), nullable=True),
        sa.Column("primary_text", sa.Text(), nullable=True),
        sa.Column("headline", sa.String(length=512), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("cta", sa.String(length=120), nullable=True),
        sa.Column("tone", sa.String(length=120), nullable=True),
        sa.Column("audience", sa.Text(), nullable=True),
        sa.Column("aspect_ratio", sa.String(length=16), nullable=True),
        sa.Column("visual_direction", sa.JSON(), nullable=False, server_default=EMPTY_DICT),
        sa.Column("image_prompt", sa.Text(), nullable=True),
        sa.Column("video_prompt", sa.Text(), nullable=True),
        sa.Column("negative_constraints", sa.JSON(), nullable=False, server_default=EMPTY_LIST),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="DRAFT"),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("meta", sa.JSON(), nullable=False, server_default=EMPTY_DICT),
        sa.Column("data_source", sa.String(length=32), nullable=False, server_default="live"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index(
        "ix_creative_variations_organization_id", "creative_variations", ["organization_id"]
    )
    op.create_index("ix_creative_variations_client_id", "creative_variations", ["client_id"])
    op.create_index(
        "ix_creative_variations_parent_concept_id", "creative_variations", ["parent_concept_id"]
    )
    op.create_index("ix_creative_variations_campaign_id", "creative_variations", ["campaign_id"])
    op.create_index("ix_creative_variations_axis", "creative_variations", ["axis"])
    op.create_index("ix_creative_variations_status", "creative_variations", ["status"])

    # ------------------------------------------------------------------
    # campaigns — internal review lifecycle and budget shape
    # ------------------------------------------------------------------
    op.add_column(
        "campaigns",
        sa.Column("review_status", sa.String(length=32), nullable=False, server_default="DRAFT"),
    )
    op.create_index("ix_campaigns_review_status", "campaigns", ["review_status"])
    op.add_column("campaigns", sa.Column("brief_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.create_foreign_key(
        "fk_campaigns_brief_id",
        "campaigns",
        "campaign_briefs",
        ["brief_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.add_column("campaigns", sa.Column("audience", sa.Text(), nullable=True))
    op.add_column("campaigns", sa.Column("total_budget", sa.Numeric(precision=12, scale=2), nullable=True))
    op.add_column("campaigns", sa.Column("daily_budget", sa.Numeric(precision=12, scale=2), nullable=True))
    op.add_column("campaigns", sa.Column("monthly_budget", sa.Numeric(precision=12, scale=2), nullable=True))
    op.add_column("campaigns", sa.Column("currency", sa.String(length=8), nullable=False, server_default="USD"))
    op.add_column(
        "campaigns",
        sa.Column("generated_by_ai", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column("campaigns", sa.Column("external_id", sa.String(length=255), nullable=True))
    op.add_column("campaigns", sa.Column("approved_by", postgresql.UUID(as_uuid=True), nullable=True))
    op.create_foreign_key(
        "fk_campaigns_approved_by", "campaigns", "users", ["approved_by"], ["id"], ondelete="SET NULL"
    )
    op.add_column("campaigns", sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("campaigns", sa.Column("approval_comment", sa.Text(), nullable=True))
    op.add_column("campaigns", sa.Column("rejected_by", postgresql.UUID(as_uuid=True), nullable=True))
    op.create_foreign_key(
        "fk_campaigns_rejected_by", "campaigns", "users", ["rejected_by"], ["id"], ondelete="SET NULL"
    )
    op.add_column("campaigns", sa.Column("rejected_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("campaigns", sa.Column("rejection_reason", sa.Text(), nullable=True))

    # ------------------------------------------------------------------
    # ad_sets / ads — the structure a reviewer approves
    # ------------------------------------------------------------------
    op.add_column("ad_sets", sa.Column("audience", sa.Text(), nullable=True))
    op.add_column("ad_sets", sa.Column("daily_budget", sa.Numeric(precision=12, scale=2), nullable=True))
    op.add_column("ad_sets", sa.Column("optimization", sa.String(length=64), nullable=True))
    # An existing table, so the default is what makes NOT NULL safe for rows that
    # predate the column.
    op.add_column(
        "ad_sets",
        sa.Column("placements", sa.JSON(), nullable=False, server_default=EMPTY_LIST),
    )

    op.add_column("ads", sa.Column("concept_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.create_foreign_key(
        "fk_ads_concept_id", "ads", "creative_concepts", ["concept_id"], ["id"], ondelete="SET NULL"
    )
    op.add_column("ads", sa.Column("variation_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.create_foreign_key(
        "fk_ads_variation_id",
        "ads",
        "creative_variations",
        ["variation_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.add_column("ads", sa.Column("creative_asset_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.create_foreign_key(
        "fk_ads_creative_asset_id",
        "ads",
        "creative_assets",
        ["creative_asset_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.add_column("ads", sa.Column("headline", sa.String(length=512), nullable=True))
    op.add_column("ads", sa.Column("primary_text", sa.Text(), nullable=True))
    op.add_column("ads", sa.Column("cta", sa.String(length=120), nullable=True))
    op.add_column("ads", sa.Column("destination", sa.String(length=512), nullable=True))

    # ------------------------------------------------------------------
    # media tables — link a generated file back to the hypothesis it tests
    # ------------------------------------------------------------------
    op.add_column("creative_assets", sa.Column("concept_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.create_foreign_key(
        "fk_creative_assets_concept_id",
        "creative_assets",
        "creative_concepts",
        ["concept_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.add_column("creative_assets", sa.Column("variation_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.create_foreign_key(
        "fk_creative_assets_variation_id",
        "creative_assets",
        "creative_variations",
        ["variation_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.add_column("creative_assets", sa.Column("aspect_ratio", sa.String(length=16), nullable=True))
    op.add_column("creative_assets", sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True))

    for table in ("image_jobs", "video_jobs"):
        op.add_column(table, sa.Column("concept_id", postgresql.UUID(as_uuid=True), nullable=True))
        op.create_foreign_key(
            f"fk_{table}_concept_id",
            table,
            "creative_concepts",
            ["concept_id"],
            ["id"],
            ondelete="SET NULL",
        )
        op.add_column(table, sa.Column("variation_id", postgresql.UUID(as_uuid=True), nullable=True))
        op.create_foreign_key(
            f"fk_{table}_variation_id",
            table,
            "creative_variations",
            ["variation_id"],
            ["id"],
            ondelete="SET NULL",
        )
        op.add_column(table, sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=True))
        op.create_foreign_key(
            f"fk_{table}_run_id",
            table,
            "campaign_generation_runs",
            ["run_id"],
            ["id"],
            ondelete="SET NULL",
        )


def downgrade() -> None:
    for table in ("video_jobs", "image_jobs"):
        op.drop_constraint(f"fk_{table}_run_id", table, type_="foreignkey")
        op.drop_column(table, "run_id")
        op.drop_constraint(f"fk_{table}_variation_id", table, type_="foreignkey")
        op.drop_column(table, "variation_id")
        op.drop_constraint(f"fk_{table}_concept_id", table, type_="foreignkey")
        op.drop_column(table, "concept_id")

    op.drop_column("creative_assets", "archived_at")
    op.drop_column("creative_assets", "aspect_ratio")
    op.drop_constraint("fk_creative_assets_variation_id", "creative_assets", type_="foreignkey")
    op.drop_column("creative_assets", "variation_id")
    op.drop_constraint("fk_creative_assets_concept_id", "creative_assets", type_="foreignkey")
    op.drop_column("creative_assets", "concept_id")

    op.drop_column("ads", "destination")
    op.drop_column("ads", "cta")
    op.drop_column("ads", "primary_text")
    op.drop_column("ads", "headline")
    op.drop_constraint("fk_ads_creative_asset_id", "ads", type_="foreignkey")
    op.drop_column("ads", "creative_asset_id")
    op.drop_constraint("fk_ads_variation_id", "ads", type_="foreignkey")
    op.drop_column("ads", "variation_id")
    op.drop_constraint("fk_ads_concept_id", "ads", type_="foreignkey")
    op.drop_column("ads", "concept_id")

    op.drop_column("ad_sets", "placements")
    op.drop_column("ad_sets", "optimization")
    op.drop_column("ad_sets", "daily_budget")
    op.drop_column("ad_sets", "audience")

    op.drop_column("campaigns", "rejection_reason")
    op.drop_column("campaigns", "rejected_at")
    op.drop_constraint("fk_campaigns_rejected_by", "campaigns", type_="foreignkey")
    op.drop_column("campaigns", "rejected_by")
    op.drop_column("campaigns", "approval_comment")
    op.drop_column("campaigns", "approved_at")
    op.drop_constraint("fk_campaigns_approved_by", "campaigns", type_="foreignkey")
    op.drop_column("campaigns", "approved_by")
    op.drop_column("campaigns", "external_id")
    op.drop_column("campaigns", "generated_by_ai")
    op.drop_column("campaigns", "currency")
    op.drop_column("campaigns", "monthly_budget")
    op.drop_column("campaigns", "daily_budget")
    op.drop_column("campaigns", "total_budget")
    op.drop_column("campaigns", "audience")
    op.drop_constraint("fk_campaigns_brief_id", "campaigns", type_="foreignkey")
    op.drop_column("campaigns", "brief_id")
    op.drop_index("ix_campaigns_review_status", table_name="campaigns")
    op.drop_column("campaigns", "review_status")

    op.drop_table("creative_variations")
    op.drop_table("creative_concepts")
    op.drop_table("campaign_generation_runs")
    op.drop_table("campaign_briefs")
