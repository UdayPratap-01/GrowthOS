"""SEO continuous monitoring (M9.15).

Revision ID: c3a8f1e2b904
Revises: b2c9d4e5f106
Create Date: 2026-09-28
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "c3a8f1e2b904"
down_revision: Union[str, None] = "b2c9d4e5f106"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "seo_monitoring_config",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.Column("monitoring_enabled", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("site_root_url", sa.String(length=2048), nullable=True),
        sa.Column("crawl_monitoring_enabled", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("search_console_monitoring_enabled", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("competitor_monitoring_enabled", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("crawl_interval_hours", sa.Integer(), nullable=False, server_default="168"),
        sa.Column("search_console_interval_hours", sa.Integer(), nullable=False, server_default="24"),
        sa.Column("competitor_interval_hours", sa.Integer(), nullable=False, server_default="168"),
        sa.Column("alert_cooldown_hours", sa.Integer(), nullable=False, server_default="24"),
        sa.Column("click_decline_threshold_pct", sa.Float(), nullable=False, server_default="20"),
        sa.Column("impression_decline_threshold_pct", sa.Float(), nullable=False, server_default="20"),
        sa.Column("position_change_threshold", sa.Float(), nullable=False, server_default="3"),
        sa.Column("high_finding_increase_threshold", sa.Integer(), nullable=False, server_default="5"),
        sa.Column("max_competitors_per_cycle", sa.Integer(), nullable=False, server_default="3"),
        sa.Column("last_crawl_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_sync_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_competitor_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_monitor_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_failure_reason", sa.String(length=512), nullable=True),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("organization_id", name="uq_seo_monitoring_config_org"),
    )
    op.create_index("ix_seo_monitoring_config_organization_id", "seo_monitoring_config", ["organization_id"])

    op.create_table(
        "seo_monitoring_runs",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.Column("run_type", sa.String(length=32), nullable=False, server_default="aggregate"),
        sa.Column("trigger", sa.String(length=16), nullable=False, server_default="scheduler"),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="queued"),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("failure_reason", sa.String(length=512), nullable=True),
        sa.Column("records_evaluated", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("changes_detected", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("alerts_generated", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("background_job_id", sa.UUID(), nullable=True),
        sa.Column("meta", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_seo_monitoring_runs_organization_id", "seo_monitoring_runs", ["organization_id"])
    op.create_index("ix_seo_monitoring_runs_status", "seo_monitoring_runs", ["status"])
    op.create_index("ix_seo_monitoring_runs_org_created", "seo_monitoring_runs", ["organization_id", "created_at"])
    op.create_index("ix_seo_monitoring_runs_org_status", "seo_monitoring_runs", ["organization_id", "status"])

    op.create_table(
        "seo_monitoring_alerts",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.Column("monitoring_run_id", sa.UUID(), nullable=True),
        sa.Column("alert_type", sa.String(length=64), nullable=False),
        sa.Column("severity", sa.String(length=16), nullable=False, server_default="medium"),
        sa.Column("title", sa.String(length=256), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False, server_default=""),
        sa.Column("entity_type", sa.String(length=64), nullable=True),
        sa.Column("entity_id", sa.String(length=64), nullable=True),
        sa.Column("affected_urls", sa.JSON(), nullable=False),
        sa.Column("evidence", sa.JSON(), nullable=False),
        sa.Column("threshold", sa.String(length=64), nullable=True),
        sa.Column("observed_value", sa.String(length=64), nullable=True),
        sa.Column("previous_value", sa.String(length=64), nullable=True),
        sa.Column("delta", sa.String(length=64), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="open"),
        sa.Column("dedupe_key", sa.String(length=256), nullable=False),
        sa.Column("acknowledged_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["monitoring_run_id"], ["seo_monitoring_runs.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("organization_id", "dedupe_key", name="uq_seo_monitoring_alerts_org_dedupe"),
    )
    op.create_index("ix_seo_monitoring_alerts_organization_id", "seo_monitoring_alerts", ["organization_id"])
    op.create_index("ix_seo_monitoring_alerts_alert_type", "seo_monitoring_alerts", ["alert_type"])
    op.create_index("ix_seo_monitoring_alerts_status", "seo_monitoring_alerts", ["status"])
    op.create_index("ix_seo_monitoring_alerts_org_status", "seo_monitoring_alerts", ["organization_id", "status"])

    op.create_table(
        "seo_monitoring_snapshots",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.Column("snapshot_type", sa.String(length=32), nullable=False),
        sa.Column("snapshot_key", sa.String(length=128), nullable=False),
        sa.Column("metric_value", sa.JSON(), nullable=False),
        sa.Column("source_run_id", sa.String(length=64), nullable=True),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "organization_id",
            "snapshot_type",
            "snapshot_key",
            name="uq_seo_monitoring_snapshots_org_type_key",
        ),
    )
    op.create_index("ix_seo_monitoring_snapshots_organization_id", "seo_monitoring_snapshots", ["organization_id"])


def downgrade() -> None:
    op.drop_table("seo_monitoring_snapshots")
    op.drop_table("seo_monitoring_alerts")
    op.drop_table("seo_monitoring_runs")
    op.drop_table("seo_monitoring_config")
