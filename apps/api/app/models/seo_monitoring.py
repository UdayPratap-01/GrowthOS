"""SEO continuous monitoring models (M9.15)."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy import JSON
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.enums import (
    SeoMonitoringAlertSeverity,
    SeoMonitoringAlertStatus,
    SeoMonitoringAlertType,
    SeoMonitoringRunStatus,
    SeoMonitoringRunTrigger,
    SeoMonitoringRunType,
)


class SeoMonitoringConfig(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "seo_monitoring_config"
    __table_args__ = (
        UniqueConstraint("organization_id", name="uq_seo_monitoring_config_org"),
    )

    organization_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), index=True
    )
    monitoring_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    site_root_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    crawl_monitoring_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    search_console_monitoring_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    competitor_monitoring_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    crawl_interval_hours: Mapped[int] = mapped_column(Integer, default=168)
    search_console_interval_hours: Mapped[int] = mapped_column(Integer, default=24)
    competitor_interval_hours: Mapped[int] = mapped_column(Integer, default=168)
    alert_cooldown_hours: Mapped[int] = mapped_column(Integer, default=24)
    click_decline_threshold_pct: Mapped[float] = mapped_column(Float, default=20.0)
    impression_decline_threshold_pct: Mapped[float] = mapped_column(Float, default=20.0)
    position_change_threshold: Mapped[float] = mapped_column(Float, default=3.0)
    high_finding_increase_threshold: Mapped[int] = mapped_column(Integer, default=5)
    max_competitors_per_cycle: Mapped[int] = mapped_column(Integer, default=3)
    last_crawl_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_sync_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_competitor_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_monitor_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_failure_reason: Mapped[str | None] = mapped_column(String(512), nullable=True)


class SeoMonitoringRun(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "seo_monitoring_runs"
    __table_args__ = (
        Index("ix_seo_monitoring_runs_org_created", "organization_id", "created_at"),
        Index("ix_seo_monitoring_runs_org_status", "organization_id", "status"),
    )

    organization_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), index=True
    )
    run_type: Mapped[SeoMonitoringRunType] = mapped_column(
        String(32), default=SeoMonitoringRunType.aggregate.value
    )
    trigger: Mapped[SeoMonitoringRunTrigger] = mapped_column(
        String(16), default=SeoMonitoringRunTrigger.scheduler.value
    )
    status: Mapped[SeoMonitoringRunStatus] = mapped_column(
        String(16), default=SeoMonitoringRunStatus.queued.value, index=True
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    failure_reason: Mapped[str | None] = mapped_column(String(512), nullable=True)
    records_evaluated: Mapped[int] = mapped_column(Integer, default=0)
    changes_detected: Mapped[int] = mapped_column(Integer, default=0)
    alerts_generated: Mapped[int] = mapped_column(Integer, default=0)
    background_job_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True)
    meta: Mapped[dict] = mapped_column(JSON, default=dict)


class SeoMonitoringAlert(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "seo_monitoring_alerts"
    __table_args__ = (
        UniqueConstraint("organization_id", "dedupe_key", name="uq_seo_monitoring_alerts_org_dedupe"),
        Index("ix_seo_monitoring_alerts_org_status", "organization_id", "status"),
    )

    organization_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), index=True
    )
    monitoring_run_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("seo_monitoring_runs.id", ondelete="SET NULL"), nullable=True
    )
    alert_type: Mapped[SeoMonitoringAlertType] = mapped_column(String(64), index=True)
    severity: Mapped[SeoMonitoringAlertSeverity] = mapped_column(String(16), default=SeoMonitoringAlertSeverity.medium.value)
    title: Mapped[str] = mapped_column(String(256))
    summary: Mapped[str] = mapped_column(Text, default="")
    entity_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    entity_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    affected_urls: Mapped[list] = mapped_column(JSON, default=list)
    evidence: Mapped[dict] = mapped_column(JSON, default=dict)
    threshold: Mapped[str | None] = mapped_column(String(64), nullable=True)
    observed_value: Mapped[str | None] = mapped_column(String(64), nullable=True)
    previous_value: Mapped[str | None] = mapped_column(String(64), nullable=True)
    delta: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[SeoMonitoringAlertStatus] = mapped_column(
        String(16), default=SeoMonitoringAlertStatus.open.value, index=True
    )
    dedupe_key: Mapped[str] = mapped_column(String(256))
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class SeoMonitoringSnapshot(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "seo_monitoring_snapshots"
    __table_args__ = (
        UniqueConstraint(
            "organization_id",
            "snapshot_type",
            "snapshot_key",
            name="uq_seo_monitoring_snapshots_org_type_key",
        ),
    )

    organization_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), index=True
    )
    snapshot_type: Mapped[str] = mapped_column(String(32))
    snapshot_key: Mapped[str] = mapped_column(String(128))
    metric_value: Mapped[dict] = mapped_column(JSON, default=dict)
    source_run_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
