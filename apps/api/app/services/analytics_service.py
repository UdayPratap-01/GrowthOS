from datetime import date, timedelta
from decimal import Decimal
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.mode import label_metrics_source
from app.models.client import Client
from app.models.enums import ClientStatus
from app.models.leads import Lead
from app.models.marketing import AnalyticsDaily, Campaign, SocialPost
from app.schemas.analytics import (
    AnalyticsOut,
    CampaignPerfItem,
    ContentPerfItem,
    PeriodTotals,
    TimePoint,
)


def _safe_div(n: Decimal | float | int, d: Decimal | float | int) -> Decimal | None:
    d_dec = Decimal(str(d))
    if d_dec == 0:
        return None
    return (Decimal(str(n)) / d_dec).quantize(Decimal("0.01"))


def _pct_delta(current: float | int | Decimal | None, previous: float | int | Decimal | None) -> float | None:
    if current is None or previous is None:
        return None
    prev = float(previous)
    if prev == 0:
        return None
    return round((float(current) - prev) / prev * 100, 2)


class AnalyticsService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def _period_totals(
        self, organization_id: UUID, client_id: UUID | None, start: date, end: date
    ) -> PeriodTotals:
        stmt = select(
            func.coalesce(func.sum(AnalyticsDaily.spend), 0),
            func.coalesce(func.sum(AnalyticsDaily.leads), 0),
            func.coalesce(func.sum(AnalyticsDaily.revenue), 0),
            func.coalesce(func.sum(AnalyticsDaily.impressions), 0),
            func.coalesce(func.sum(AnalyticsDaily.clicks), 0),
            func.coalesce(func.sum(AnalyticsDaily.conversions), 0),
        ).where(
            AnalyticsDaily.organization_id == organization_id,
            AnalyticsDaily.date >= start,
            AnalyticsDaily.date <= end,
        )
        if client_id:
            stmt = stmt.where(AnalyticsDaily.client_id == client_id)
        spend, leads, revenue, impressions, clicks, conversions = (await self.db.execute(stmt)).one()
        spend_d = Decimal(spend)
        leads_i = int(leads or 0)
        clicks_i = int(clicks or 0)
        conversions_i = int(conversions or 0)
        impressions_i = int(impressions or 0)
        return PeriodTotals(
            spend=spend_d,
            leads=leads_i,
            revenue=Decimal(revenue),
            impressions=impressions_i,
            clicks=clicks_i,
            conversions=conversions_i,
            cpl=_safe_div(spend_d, leads_i) if leads_i else None,
            ctr=_safe_div(Decimal(clicks_i) * 100, impressions_i) if impressions_i else None,
            conversion_rate=_safe_div(Decimal(conversions_i) * 100, clicks_i) if clicks_i else None,
        )

    async def _series(
        self, organization_id: UUID, client_id: UUID | None, start: date, end: date
    ) -> dict[str, list[TimePoint]]:
        stmt = select(AnalyticsDaily).where(
            AnalyticsDaily.organization_id == organization_id,
            AnalyticsDaily.date >= start,
            AnalyticsDaily.date <= end,
        )
        if client_id:
            stmt = stmt.where(AnalyticsDaily.client_id == client_id)
        stmt = stmt.order_by(AnalyticsDaily.date.asc())
        rows = list((await self.db.execute(stmt)).scalars().all())

        # Aggregate by date when org-wide
        by_date: dict[date, dict[str, float]] = {}
        for row in rows:
            bucket = by_date.setdefault(
                row.date, {"spend": 0, "leads": 0, "clicks": 0, "impressions": 0, "conversions": 0, "revenue": 0}
            )
            bucket["spend"] += float(row.spend or 0)
            bucket["leads"] += float(row.leads or 0)
            bucket["clicks"] += float(row.clicks or 0)
            bucket["impressions"] += float(row.impressions or 0)
            bucket["conversions"] += float(row.conversions or 0)
            bucket["revenue"] += float(row.revenue or 0)

        series: dict[str, list[TimePoint]] = {
            "leads": [],
            "spend": [],
            "cpl": [],
            "ctr": [],
            "conversion_rate": [],
            "revenue": [],
        }
        cursor = start
        while cursor <= end:
            b = by_date.get(cursor, {"spend": 0, "leads": 0, "clicks": 0, "impressions": 0, "conversions": 0, "revenue": 0})
            series["leads"].append(TimePoint(date=cursor, value=b["leads"]))
            series["spend"].append(TimePoint(date=cursor, value=b["spend"]))
            series["revenue"].append(TimePoint(date=cursor, value=b["revenue"]))
            cpl = float(b["spend"] / b["leads"]) if b["leads"] else 0
            ctr = float(b["clicks"] / b["impressions"] * 100) if b["impressions"] else 0
            cvr = float(b["conversions"] / b["clicks"] * 100) if b["clicks"] else 0
            series["cpl"].append(TimePoint(date=cursor, value=round(cpl, 2)))
            series["ctr"].append(TimePoint(date=cursor, value=round(ctr, 2)))
            series["conversion_rate"].append(TimePoint(date=cursor, value=round(cvr, 2)))
            cursor += timedelta(days=1)
        return series

    async def get_analytics(
        self,
        organization_id: UUID,
        *,
        client_id: UUID | None,
        period_days: int,
        demo_mode: bool,
    ) -> AnalyticsOut:
        if client_id:
            client = await self.db.scalar(
                select(Client).where(
                    Client.id == client_id,
                    Client.organization_id == organization_id,
                    Client.status == ClientStatus.active,
                )
            )
            if not client:
                from fastapi import HTTPException, status

                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Client not found")

        end = date.today()
        start = end - timedelta(days=period_days - 1)
        prev_end = start - timedelta(days=1)
        prev_start = prev_end - timedelta(days=period_days - 1)

        current = await self._period_totals(organization_id, client_id, start, end)
        previous = await self._period_totals(organization_id, client_id, prev_start, prev_end)
        series = await self._series(organization_id, client_id, start, end)

        insufficient: list[str] = []
        if current.spend == 0 and current.leads == 0:
            insufficient.append("No analytics rows in selected period — Insufficient data.")
        if current.impressions == 0:
            insufficient.append("impressions/CTR")
        if current.leads == 0:
            insufficient.append("CPL")

        content_items: list[ContentPerfItem] = []
        if client_id:
            posts = list(
                (
                    await self.db.execute(
                        select(SocialPost)
                        .where(
                            SocialPost.organization_id == organization_id,
                            SocialPost.client_id == client_id,
                        )
                        .order_by(SocialPost.created_at.desc())
                        .limit(12)
                    )
                ).scalars().all()
            )
            for p in posts:
                metrics = p.metrics or {}
                content_items.append(
                    ContentPerfItem(
                        id=p.id,
                        platform=p.platform,
                        content_type=p.content_type,
                        hook=p.hook,
                        impressions=metrics.get("impressions"),
                        engagement=metrics.get("engagement"),
                        ctr=metrics.get("ctr"),
                        data_source=p.data_source.value if hasattr(p.data_source, "value") else str(p.data_source),
                        note=None
                        if metrics.get("impressions") is not None
                        else "Insufficient data for engagement metrics.",
                    )
                )

        campaign_items: list[CampaignPerfItem] = []
        camp_stmt = select(Campaign).where(Campaign.organization_id == organization_id)
        if client_id:
            camp_stmt = camp_stmt.where(Campaign.client_id == client_id)
        camps = list((await self.db.execute(camp_stmt.limit(20))).scalars().all())
        for c in camps:
            metrics = c.metrics or {}
            leads = int(metrics.get("leads") or 0)
            spend = Decimal(c.spend or 0)
            campaign_items.append(
                CampaignPerfItem(
                    id=c.id,
                    name=c.name,
                    platform=c.platform,
                    spend=spend,
                    leads=leads,
                    cpl=_safe_div(spend, leads) if leads else (Decimal(str(metrics["cpl"])) if metrics.get("cpl") else None),
                    ctr=float(metrics["ctr"]) if metrics.get("ctr") is not None else None,
                    status=c.status,
                    data_source=c.data_source.value if hasattr(c.data_source, "value") else str(c.data_source),
                )
            )

        # Lead funnel section from CRM (not invented)
        lead_stmt = select(Lead.status, func.count()).where(Lead.organization_id == organization_id)
        if client_id:
            lead_stmt = lead_stmt.where(Lead.client_id == client_id)
        lead_stmt = lead_stmt.group_by(Lead.status)
        lead_rows = (await self.db.execute(lead_stmt)).all()
        lead_funnel = {str(status.value if hasattr(status, "value") else status): int(count) for status, count in lead_rows}

        deltas = {
            "spend": _pct_delta(current.spend, previous.spend),
            "leads": _pct_delta(current.leads, previous.leads),
            "revenue": _pct_delta(current.revenue, previous.revenue),
            "cpl": _pct_delta(current.cpl, previous.cpl),
            "ctr": _pct_delta(current.ctr, previous.ctr),
            "conversion_rate": _pct_delta(current.conversion_rate, previous.conversion_rate),
        }

        src_stmt = select(AnalyticsDaily.data_source).where(
            AnalyticsDaily.organization_id == organization_id,
            AnalyticsDaily.date >= start,
            AnalyticsDaily.date <= end,
        )
        if client_id:
            src_stmt = src_stmt.where(AnalyticsDaily.client_id == client_id)
        source_rows = (await self.db.execute(src_stmt.distinct())).scalars().all()
        row_sources = {r.value if hasattr(r, "value") else str(r) for r in source_rows}
        metrics_source = label_metrics_source(org_demo=demo_mode, row_sources=row_sources)
        if metrics_source == "mixed":
            insufficient.append(
                "Metrics include seed/demo rows while organization is not in pure demo mode (mixed)."
            )

        return AnalyticsOut(
            client_id=client_id,
            period_days=period_days,
            comparison_period_days=period_days,
            data_source=metrics_source,
            demo_mode=demo_mode or metrics_source in {"demo", "mixed"},
            current=current,
            previous=previous,
            deltas=deltas,
            series=series,
            content_performance=content_items,
            campaign_performance=campaign_items,
            insufficient_data=insufficient,
            sections={
                "social": {
                    "impressions": current.impressions,
                    "clicks": current.clicks,
                    "ctr": float(current.ctr) if current.ctr is not None else None,
                },
                "campaigns": {
                    "spend": float(current.spend),
                    "campaign_count": len(campaign_items),
                },
                "leads": {
                    "total": current.leads,
                    "funnel": lead_funnel,
                    "cpl": float(current.cpl) if current.cpl is not None else None,
                },
                "conversions": {
                    "conversions": current.conversions,
                    "conversion_rate": float(current.conversion_rate) if current.conversion_rate is not None else None,
                    "revenue": float(current.revenue),
                },
            },
        )
