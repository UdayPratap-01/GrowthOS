from __future__ import annotations

from datetime import date, datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class SearchConsoleSyncRequest(BaseModel):
    preset: str | None = Field(default="last_28_days", pattern="^(last_7_days|last_28_days|last_90_days|custom)$")
    start_date: date | None = None
    end_date: date | None = None
    site_url: str | None = None
    client_id: UUID | None = None
    include_comparison: bool = True


class SearchConsolePropertyOut(BaseModel):
    site_url: str
    permission_level: str | None = None
    connected: bool = True


class SearchConsoleSyncOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    site_url: str
    status: str
    preset: str | None = None
    requested_start_date: date
    requested_end_date: date
    effective_start_date: date
    effective_end_date: date
    compare_start_date: date | None = None
    compare_end_date: date | None = None
    row_count: int
    opportunity_count: int
    error: str | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    meta: dict = Field(default_factory=dict)


class SearchConsolePerformanceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    dimension_type: str
    query: str | None = None
    page_url: str | None = None
    country: str | None = None
    device: str | None = None
    start_date: date
    end_date: date
    clicks: float
    impressions: float
    ctr: float
    average_position: float
    compare_clicks: float | None = None
    compare_impressions: float | None = None
    compare_ctr: float | None = None
    compare_position: float | None = None
    metrics_delta: dict = Field(default_factory=dict)
    data_source: str


class SearchConsoleOpportunityOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    sync_id: UUID
    site_url: str
    opportunity_type: str
    rule_id: str
    priority: str
    status: str
    query: str | None = None
    page_url: str | None = None
    date_range_start: date
    date_range_end: date
    clicks: float
    impressions: float
    ctr: float
    average_position: float
    comparison_metrics: dict = Field(default_factory=dict)
    evidence: dict = Field(default_factory=dict)
    explanation: str
    created_at: datetime | None = None


class SearchConsoleSummaryOut(BaseModel):
    site_url: str | None = None
    connected: bool
    last_sync: SearchConsoleSyncOut | None = None
    totals: dict = Field(default_factory=dict)
    by_priority: dict = Field(default_factory=dict)
    opportunity_count: int = 0
    disclaimer: str
