from __future__ import annotations

from datetime import date, datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class KeywordAnalyzeRequest(BaseModel):
    sync_id: UUID | None = None
    brand_terms: list[str] = Field(default_factory=list)


class KeywordOpportunityOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    sync_id: UUID
    site_url: str
    query: str
    normalized_query: str
    page_url: str | None = None
    opportunity_type: str
    rule_id: str
    status: str
    priority: str
    priority_score: float
    clicks: float
    impressions: float
    ctr: float
    average_position: float
    previous_clicks: float | None = None
    previous_impressions: float | None = None
    previous_ctr: float | None = None
    previous_average_position: float | None = None
    change_metrics: dict = Field(default_factory=dict)
    query_classification: dict = Field(default_factory=dict)
    page_associations: list = Field(default_factory=list)
    evidence: dict = Field(default_factory=dict)
    explanation: str
    date_start: date
    date_end: date
    data_source: str
    created_at: datetime | None = None


class KeywordSummaryOut(BaseModel):
    sync_id: UUID | None = None
    site_url: str | None = None
    unique_queries: int
    total_opportunities: int
    by_type: dict[str, int]
    by_priority: dict[str, int]
    near_page_one_count: int
    high_impression_low_ctr_count: int
    multi_page_query_count: int
    improving_count: int
    declining_count: int
    disclaimer: str
