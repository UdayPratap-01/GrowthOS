from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, Field


class TimePoint(BaseModel):
    date: date
    value: float


class PeriodTotals(BaseModel):
    spend: Decimal
    leads: int
    revenue: Decimal
    impressions: int
    clicks: int
    conversions: int
    cpl: Decimal | None
    ctr: Decimal | None
    conversion_rate: Decimal | None


class ContentPerfItem(BaseModel):
    id: UUID
    platform: str
    content_type: str
    hook: str | None
    impressions: int | None
    engagement: int | None
    ctr: float | None
    data_source: str
    note: str | None = None


class CampaignPerfItem(BaseModel):
    id: UUID
    name: str
    platform: str
    spend: Decimal
    leads: int
    cpl: Decimal | None
    ctr: float | None
    status: str
    data_source: str


class AnalyticsOut(BaseModel):
    client_id: UUID | None
    period_days: int
    comparison_period_days: int
    data_source: str
    demo_mode: bool
    current: PeriodTotals
    previous: PeriodTotals
    deltas: dict[str, float | None]
    series: dict[str, list[TimePoint]]
    content_performance: list[ContentPerfItem] = Field(default_factory=list)
    campaign_performance: list[CampaignPerfItem] = Field(default_factory=list)
    insufficient_data: list[str] = Field(default_factory=list)
    sections: dict[str, dict] = Field(default_factory=dict)


class PerformanceRowOut(BaseModel):
    id: UUID
    organization_id: UUID
    client_id: UUID | None
    platform: str
    entity_level: str
    external_account_id: str
    external_campaign_id: str
    external_ad_set_id: str
    external_ad_id: str
    date: date
    granularity: str
    impressions: int
    reach: int | None
    clicks: int
    spend: Decimal
    conversions: Decimal
    leads: int
    revenue: Decimal
    ctr: Decimal | None
    cpc: Decimal | None
    cpm: Decimal | None
    cpl: Decimal | None
    cpa: Decimal | None
    roas: Decimal | None
    currency: str
    data_source: str
    ingested_at: datetime | None
    provider_metadata: dict = Field(default_factory=dict)


class PerformanceListOut(BaseModel):
    items: list[PerformanceRowOut]
    total: int
    limit: int
    offset: int


class IngestRequest(BaseModel):
    provider: str = Field(..., description="meta | google_ads")
    client_id: UUID | None = None
    lookback_days: int | None = Field(default=None, ge=1, le=30)
    entity_level: str = Field(default="campaign")


class IngestEnqueueOut(BaseModel):
    job_id: UUID
    job_type: str
    provider: str
    client_id: UUID | None
    lookback_days: int
    dedupe_key: str | None = None


class AnalyzeRequest(BaseModel):
    client_id: UUID | None = None
    window_days: int = Field(default=7, description="7 | 14 | 30")
    platform: str | None = None
    entity_level: str = Field(default="campaign")
    use_ai_explanation: bool = True


class AnalyzeEnqueueOut(BaseModel):
    job_id: UUID
    job_type: str
    window_days: int
    client_id: UUID | None
    platform: str | None = None
    dedupe_key: str | None = None


class PerformanceRecommendationOut(BaseModel):
    id: UUID
    organization_id: UUID
    client_id: UUID | None
    platform: str
    entity_level: str
    external_account_id: str
    external_campaign_id: str
    external_ad_set_id: str
    external_ad_id: str
    recommendation_type: str
    severity: str
    title: str
    explanation: str
    evidence: list
    affected_metrics: list
    current_values: dict
    comparison_values: dict
    percentage_changes: dict
    confidence: Decimal
    suggested_action: dict
    signal_category: str
    analysis_window_days: int
    window_current_start: date
    window_current_end: date
    window_previous_start: date
    window_previous_end: date
    status: str
    explanation_source: str
    expires_at: datetime | None
    reviewed_at: datetime | None
    created_at: datetime | None = None


class PerformanceRecommendationListOut(BaseModel):
    items: list[PerformanceRecommendationOut]
    total: int
    limit: int
    offset: int


class PerformanceRecommendationStatusUpdate(BaseModel):
    status: str = Field(..., description="NEW | REVIEWED | APPROVED | REJECTED | EXPIRED")
