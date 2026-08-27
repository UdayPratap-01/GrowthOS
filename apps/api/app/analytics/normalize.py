"""Provider-neutral performance row used before persistence."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any
from uuid import UUID

from app.analytics.metrics import compute_derived_metrics
from app.automation.idempotency import sanitize_platform_response

ENTITY_LEVELS = frozenset({"account", "campaign", "ad_set", "ad"})
SUPPORTED_PLATFORMS = frozenset({"meta", "google_ads"})
GRANULARITY_DAILY = "daily"


@dataclass
class NormalizedPerformanceRow:
    organization_id: UUID
    platform: str
    entity_level: str
    date: date
    external_account_id: str = ""
    external_campaign_id: str = ""
    external_ad_set_id: str = ""
    external_ad_id: str = ""
    client_id: UUID | None = None
    impressions: int = 0
    reach: int | None = None
    clicks: int = 0
    spend: Decimal = Decimal("0")
    conversions: Decimal = Decimal("0")
    leads: int = 0
    revenue: Decimal = Decimal("0")
    currency: str = "USD"
    granularity: str = GRANULARITY_DAILY
    provider_metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.platform = (self.platform or "").strip().lower()
        if self.platform in {"facebook", "instagram"}:
            self.platform = "meta"
        if self.platform == "google":
            self.platform = "google_ads"
        self.entity_level = (self.entity_level or "campaign").strip().lower()
        self.external_account_id = str(self.external_account_id or "")
        self.external_campaign_id = str(self.external_campaign_id or "")
        self.external_ad_set_id = str(self.external_ad_set_id or "")
        self.external_ad_id = str(self.external_ad_id or "")
        self.granularity = (self.granularity or GRANULARITY_DAILY).strip().lower()
        self.impressions = max(0, int(self.impressions or 0))
        self.clicks = max(0, int(self.clicks or 0))
        self.leads = max(0, int(self.leads or 0))
        if self.reach is not None:
            self.reach = max(0, int(self.reach))
        self.spend = Decimal(str(self.spend or 0))
        self.conversions = Decimal(str(self.conversions or 0))
        self.revenue = Decimal(str(self.revenue or 0))
        self.provider_metadata = sanitize_platform_response(self.provider_metadata or {})

    def derived(self):
        return compute_derived_metrics(
            impressions=self.impressions,
            clicks=self.clicks,
            spend=self.spend,
            conversions=self.conversions,
            leads=self.leads,
            revenue=self.revenue,
        )

    def natural_key(self) -> tuple:
        return (
            self.organization_id,
            self.platform,
            self.entity_level,
            self.external_account_id,
            self.external_campaign_id,
            self.external_ad_set_id,
            self.external_ad_id,
            self.date,
            self.granularity,
        )
