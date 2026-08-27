"""Comparison windows and metric aggregation over MarketingPerformanceDaily."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.analytics.metrics import compute_derived_metrics
from app.models.marketing import MarketingPerformanceDaily

SUPPORTED_WINDOWS = (7, 14, 30)

VOLUME_METRICS = (
    "impressions",
    "clicks",
    "spend",
    "conversions",
    "leads",
    "revenue",
    "reach",
)
RATE_METRICS = ("ctr", "cpc", "cpm", "cpl", "cpa", "roas")
ALL_METRICS = VOLUME_METRICS + RATE_METRICS


@dataclass(frozen=True)
class AnalysisWindow:
    days: int
    current_start: date
    current_end: date
    previous_start: date
    previous_end: date

    @classmethod
    def for_days(cls, days: int, *, as_of: date | None = None) -> AnalysisWindow:
        if days not in SUPPORTED_WINDOWS:
            raise ValueError(f"Unsupported analysis window {days}; expected one of {SUPPORTED_WINDOWS}")
        end = as_of or date.today()
        current_start = end - timedelta(days=days - 1)
        previous_end = current_start - timedelta(days=1)
        previous_start = previous_end - timedelta(days=days - 1)
        return cls(
            days=days,
            current_start=current_start,
            current_end=end,
            previous_start=previous_start,
            previous_end=previous_end,
        )


@dataclass
class PeriodTotals:
    impressions: int = 0
    reach: int | None = None
    clicks: int = 0
    spend: Decimal = Decimal("0")
    conversions: Decimal = Decimal("0")
    leads: int = 0
    revenue: Decimal = Decimal("0")
    days_with_data: int = 0
    row_count: int = 0

    def as_dict(self) -> dict[str, Any]:
        derived = compute_derived_metrics(
            impressions=self.impressions,
            clicks=self.clicks,
            spend=self.spend,
            conversions=self.conversions,
            leads=self.leads,
            revenue=self.revenue,
        )
        return {
            "impressions": self.impressions,
            "reach": self.reach,
            "clicks": self.clicks,
            "spend": float(self.spend),
            "conversions": float(self.conversions),
            "leads": self.leads,
            "revenue": float(self.revenue),
            "ctr": float(derived.ctr) if derived.ctr is not None else None,
            "cpc": float(derived.cpc) if derived.cpc is not None else None,
            "cpm": float(derived.cpm) if derived.cpm is not None else None,
            "cpl": float(derived.cpl) if derived.cpl is not None else None,
            "cpa": float(derived.cpa) if derived.cpa is not None else None,
            "roas": float(derived.roas) if derived.roas is not None else None,
            "days_with_data": self.days_with_data,
            "row_count": self.row_count,
        }


@dataclass
class EntityPeriodComparison:
    organization_id: UUID
    client_id: UUID | None
    platform: str
    entity_level: str
    external_account_id: str
    external_campaign_id: str
    external_ad_set_id: str
    external_ad_id: str
    window: AnalysisWindow
    current: PeriodTotals
    previous: PeriodTotals
    percentage_changes: dict[str, float | None] = field(default_factory=dict)
    insufficient_data: bool = False
    insufficient_reasons: list[str] = field(default_factory=list)


def pct_change(current: float | Decimal | None, previous: float | Decimal | None) -> float | None:
    if current is None or previous is None:
        return None
    cur = float(current)
    prev = float(previous)
    if prev == 0:
        if cur == 0:
            return 0.0
        return None  # undefined vs zero baseline
    return ((cur - prev) / abs(prev)) * 100.0


def aggregate_rows(rows: list[MarketingPerformanceDaily]) -> PeriodTotals:
    totals = PeriodTotals()
    if not rows:
        return totals
    reach_sum = 0
    reach_seen = False
    days: set[date] = set()
    for row in rows:
        totals.impressions += int(row.impressions or 0)
        totals.clicks += int(row.clicks or 0)
        totals.spend += Decimal(str(row.spend or 0))
        totals.conversions += Decimal(str(row.conversions or 0))
        totals.leads += int(row.leads or 0)
        totals.revenue += Decimal(str(row.revenue or 0))
        if row.reach is not None:
            reach_sum += int(row.reach)
            reach_seen = True
        days.add(row.date)
        totals.row_count += 1
    totals.days_with_data = len(days)
    totals.reach = reach_sum if reach_seen else None
    return totals


def build_percentage_changes(current: dict[str, Any], previous: dict[str, Any]) -> dict[str, float | None]:
    out: dict[str, float | None] = {}
    for metric in ALL_METRICS:
        out[metric] = pct_change(current.get(metric), previous.get(metric))
    return out


async def load_entity_comparisons(
    db: AsyncSession,
    *,
    organization_id: UUID,
    client_id: UUID | None,
    window: AnalysisWindow,
    platform: str | None = None,
    entity_level: str = "campaign",
) -> list[EntityPeriodComparison]:
    filters = [
        MarketingPerformanceDaily.organization_id == organization_id,
        MarketingPerformanceDaily.entity_level == entity_level,
        MarketingPerformanceDaily.date >= window.previous_start,
        MarketingPerformanceDaily.date <= window.current_end,
    ]
    if client_id is not None:
        filters.append(MarketingPerformanceDaily.client_id == client_id)
    if platform:
        filters.append(MarketingPerformanceDaily.platform == platform.strip().lower())

    rows = list((await db.scalars(select(MarketingPerformanceDaily).where(*filters))).all())
    buckets: dict[tuple, list[MarketingPerformanceDaily]] = {}
    for row in rows:
        key = (
            row.platform,
            row.entity_level,
            row.external_account_id or "",
            row.external_campaign_id or "",
            row.external_ad_set_id or "",
            row.external_ad_id or "",
            row.client_id,
        )
        buckets.setdefault(key, []).append(row)

    comparisons: list[EntityPeriodComparison] = []
    for key, entity_rows in buckets.items():
        platform_key, level, acct, camp, adset, ad, cid = key
        current_rows = [r for r in entity_rows if window.current_start <= r.date <= window.current_end]
        previous_rows = [r for r in entity_rows if window.previous_start <= r.date <= window.previous_end]
        current = aggregate_rows(current_rows)
        previous = aggregate_rows(previous_rows)
        cur_dict = current.as_dict()
        prev_dict = previous.as_dict()
        comparisons.append(
            EntityPeriodComparison(
                organization_id=organization_id,
                client_id=cid,
                platform=platform_key,
                entity_level=level,
                external_account_id=acct,
                external_campaign_id=camp,
                external_ad_set_id=adset,
                external_ad_id=ad,
                window=window,
                current=current,
                previous=previous,
                percentage_changes=build_percentage_changes(cur_dict, prev_dict),
            )
        )
    return comparisons
