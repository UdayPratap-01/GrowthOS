"""Pydantic schemas for SEO continuous monitoring (M9.15)."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


class SeoMonitoringConfigOut(BaseModel):
    organization_id: UUID
    monitoring_enabled: bool
    site_root_url: str | None = None
    crawl_monitoring_enabled: bool
    search_console_monitoring_enabled: bool
    competitor_monitoring_enabled: bool
    crawl_interval_hours: int
    search_console_interval_hours: int
    competitor_interval_hours: int
    alert_cooldown_hours: int
    click_decline_threshold_pct: float
    impression_decline_threshold_pct: float
    position_change_threshold: float
    high_finding_increase_threshold: int
    max_competitors_per_cycle: int
    last_crawl_at: datetime | None = None
    last_sync_at: datetime | None = None
    last_competitor_at: datetime | None = None
    last_monitor_run_at: datetime | None = None
    last_failure_reason: str | None = None


class SeoMonitoringConfigPatch(BaseModel):
    monitoring_enabled: bool | None = None
    site_root_url: str | None = None
    crawl_monitoring_enabled: bool | None = None
    search_console_monitoring_enabled: bool | None = None
    competitor_monitoring_enabled: bool | None = None
    crawl_interval_hours: int | None = Field(default=None, ge=1, le=24 * 30)
    search_console_interval_hours: int | None = Field(default=None, ge=1, le=24 * 30)
    competitor_interval_hours: int | None = Field(default=None, ge=1, le=24 * 30)
    alert_cooldown_hours: int | None = Field(default=None, ge=1, le=24 * 7)
    click_decline_threshold_pct: float | None = Field(default=None, ge=1, le=100)
    impression_decline_threshold_pct: float | None = Field(default=None, ge=1, le=100)
    position_change_threshold: float | None = Field(default=None, ge=0.1, le=50)
    high_finding_increase_threshold: int | None = Field(default=None, ge=1, le=100)
    max_competitors_per_cycle: int | None = Field(default=None, ge=1, le=10)


class SeoMonitoringStatusOut(BaseModel):
    config: SeoMonitoringConfigOut
    open_alerts: int
    scheduler_enabled: bool
    next_scheduled_run: datetime | None = None
    last_run_status: str | None = None
    last_run_at: datetime | None = None
    disclaimer: str


class SeoMonitoringRunOut(BaseModel):
    id: UUID
    organization_id: UUID
    run_type: str
    trigger: str
    status: str
    started_at: datetime | None = None
    completed_at: datetime | None = None
    failure_reason: str | None = None
    records_evaluated: int
    changes_detected: int
    alerts_generated: int
    background_job_id: UUID | None = None
    created_at: datetime


class SeoMonitoringRunListOut(BaseModel):
    items: list[SeoMonitoringRunOut]
    total: int
    limit: int
    offset: int


class SeoMonitoringAlertOut(BaseModel):
    id: UUID
    organization_id: UUID
    monitoring_run_id: UUID | None = None
    alert_type: str
    severity: str
    title: str
    summary: str
    entity_type: str | None = None
    entity_id: str | None = None
    affected_urls: list[str] = Field(default_factory=list)
    threshold: str | None = None
    observed_value: str | None = None
    previous_value: str | None = None
    delta: str | None = None
    status: str
    created_at: datetime
    acknowledged_at: datetime | None = None
    resolved_at: datetime | None = None


class SeoMonitoringAlertListOut(BaseModel):
    items: list[SeoMonitoringAlertOut]
    total: int
    limit: int
    offset: int


class SeoMonitoringManualRunOut(BaseModel):
    job_id: UUID
    message: str
