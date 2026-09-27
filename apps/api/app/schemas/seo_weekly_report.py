"""Pydantic schemas for SEO weekly reports (M9.16)."""

from __future__ import annotations

from datetime import date, datetime
from uuid import UUID

from pydantic import BaseModel, Field


class SeoReportConfigOut(BaseModel):
    organization_id: UUID
    reporting_enabled: bool
    last_report_at: datetime | None = None
    last_failure_reason: str | None = None


class SeoReportConfigPatch(BaseModel):
    reporting_enabled: bool


class SeoWeeklyReportOut(BaseModel):
    id: UUID
    organization_id: UUID
    period_start: date
    period_end: date
    status: str
    report_version: str
    generated_at: datetime | None = None
    summary: str
    data_freshness: dict = Field(default_factory=dict)
    limitations: list[str] = Field(default_factory=list)
    error_message: str | None = None
    background_job_id: UUID | None = None
    created_at: datetime


class SeoWeeklyReportDetailOut(SeoWeeklyReportOut):
    report_payload: dict = Field(default_factory=dict)
    disclaimer: str = ""


class SeoWeeklyReportListOut(BaseModel):
    items: list[SeoWeeklyReportOut]
    total: int
    limit: int
    offset: int


class SeoWeeklyReportGenerateRequest(BaseModel):
    period_start: date | None = None
    period_end: date | None = None


class SeoWeeklyReportGenerateOut(BaseModel):
    report_id: UUID
    status: str
    message: str
    poll_job_id: UUID | None = None
