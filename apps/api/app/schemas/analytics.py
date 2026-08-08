from datetime import date
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
