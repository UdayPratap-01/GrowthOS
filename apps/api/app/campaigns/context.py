"""
Client context for the campaign engine, assembled from stored data only.

This is the one place that decides what the agents are allowed to know. Its whole
job is to separate two things that a language model will otherwise blur:

- **Evidence**: a figure that exists in this organization's tables, carried with
  the row count and date range it came from so a recommendation can cite it.
- **A gap**: a figure that does not exist. Named explicitly in
  `data_limitations` so the prompt can be told "say 'Insufficient data.'" rather
  than left to invent a plausible CTR.

A metric is never estimated, interpolated, or derived from a similar client.
Demo rows are counted as evidence only for demo organizations, and are labelled
as such, so a live campaign is never argued for on the strength of seeded data.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.enums import DataSource
from app.models.leads import Lead
from app.models.marketing import AnalyticsCampaign, Campaign, SocialPost
from app.models.organization import Organization
from app.models.strategy import Strategy
from app.schemas.client import ClientContext
from app.services.client_service import ClientService

#: How far back history is read. Older performance describes a different market
#: and a different offer, so including it would weaken rather than ground a
#: recommendation.
HISTORY_WINDOW_DAYS = 180
MAX_HISTORY_ROWS = 10


@dataclass
class CampaignContext:
    """A client context plus the honest account of what is missing from it."""

    client_context: ClientContext
    data_limitations: list[str] = field(default_factory=list)
    #: True when the only performance history available is seeded demo data.
    history_is_demo: bool = False

    @property
    def has_performance_history(self) -> bool:
        return bool(self.client_context.historical_campaign_performance)


def _num(value) -> float | None:
    if value is None:
        return None
    if isinstance(value, Decimal):
        return float(value)
    return float(value)


class CampaignContextBuilder:
    """
    Builds the campaign-engine view of a client.

    Extends `ClientService.build_client_context` rather than replacing it: the
    base context already resolves the client record and the aggregate metrics
    that exist, and duplicating that logic would let the two drift.
    """

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def build(self, organization: Organization, client_id: UUID) -> CampaignContext:
        base = await ClientService(self.db).build_client_context(organization, client_id)
        since = datetime.now(timezone.utc) - timedelta(days=HISTORY_WINDOW_DAYS)

        campaigns, campaign_demo = await self._campaign_history(organization.id, client_id, since)
        content = await self._content_history(organization.id, client_id)
        leads = await self._lead_performance(organization.id, client_id)
        strategies = await self._previous_strategies(organization.id, client_id)

        context = base.model_copy(
            update={
                "historical_campaign_performance": campaigns,
                "historical_content_performance": content,
                "lead_performance": leads,
                "previous_strategies": strategies,
            }
        )

        return CampaignContext(
            client_context=context,
            data_limitations=self._limitations(context, campaign_demo),
            history_is_demo=campaign_demo,
        )

    # -- history sources --------------------------------------------------

    async def _campaign_history(
        self, organization_id: UUID, client_id: UUID, since: datetime
    ) -> tuple[list[dict], bool]:
        """
        Per-campaign delivery totals, aggregated from `analytics_campaigns`.

        Rates are computed from the summed counts rather than averaged from the
        daily rows: averaging a ratio over days with unequal volume produces a
        number that is not the campaign's CTR.
        """
        rows = await self.db.execute(
            select(
                Campaign.id,
                Campaign.name,
                Campaign.platform,
                Campaign.objective,
                func.coalesce(func.sum(AnalyticsCampaign.spend), 0),
                func.coalesce(func.sum(AnalyticsCampaign.leads), 0),
                func.coalesce(func.sum(AnalyticsCampaign.impressions), 0),
                func.coalesce(func.sum(AnalyticsCampaign.clicks), 0),
                func.min(AnalyticsCampaign.date),
                func.max(AnalyticsCampaign.date),
                func.count(AnalyticsCampaign.id),
                func.min(AnalyticsCampaign.data_source),
            )
            .join(AnalyticsCampaign, AnalyticsCampaign.campaign_id == Campaign.id)
            .where(
                Campaign.organization_id == organization_id,
                Campaign.client_id == client_id,
                AnalyticsCampaign.created_at >= since,
            )
            .group_by(Campaign.id, Campaign.name, Campaign.platform, Campaign.objective)
            .order_by(func.sum(AnalyticsCampaign.spend).desc())
            .limit(MAX_HISTORY_ROWS)
        )

        history: list[dict] = []
        any_demo = False
        for (
            _campaign_id,
            name,
            platform,
            objective,
            spend,
            leads,
            impressions,
            clicks,
            first_date,
            last_date,
            day_count,
            source,
        ) in rows.all():
            spend_f = _num(spend) or 0.0
            leads_i = int(leads or 0)
            impressions_i = int(impressions or 0)
            clicks_i = int(clicks or 0)
            is_demo = str(getattr(source, "value", source) or "") == DataSource.demo.value
            any_demo = any_demo or is_demo

            entry: dict = {
                "campaign": name,
                "platform": platform,
                "objective": objective,
                "days_of_data": int(day_count or 0),
                "date_range": [
                    first_date.isoformat() if first_date else None,
                    last_date.isoformat() if last_date else None,
                ],
                "data_source": DataSource.demo.value if is_demo else "live",
            }
            if spend_f > 0:
                entry["spend"] = round(spend_f, 2)
            if leads_i:
                entry["leads"] = leads_i
            if impressions_i:
                entry["impressions"] = impressions_i
                entry["clicks"] = clicks_i
                if clicks_i:
                    entry["ctr_percent"] = round(clicks_i / impressions_i * 100, 2)
            if leads_i and spend_f > 0:
                entry["cpl"] = round(spend_f / leads_i, 2)
            history.append(entry)

        return history, any_demo

    async def _content_history(self, organization_id: UUID, client_id: UUID) -> list[dict]:
        """
        Published posts that carry recorded metrics.

        Posts with an empty `metrics` dict are skipped rather than reported as
        zero-performing: "never measured" and "measured at zero" are different
        facts and only one of them is a reason to change the creative approach.
        """
        posts = await self.db.scalars(
            select(SocialPost)
            .where(
                SocialPost.organization_id == organization_id,
                SocialPost.client_id == client_id,
            )
            .order_by(SocialPost.created_at.desc())
            .limit(50)
        )
        history: list[dict] = []
        for post in posts:
            metrics = post.metrics or {}
            if not metrics:
                continue
            history.append(
                {
                    "platform": post.platform,
                    "content_type": post.content_type,
                    "hook": post.hook,
                    "metrics": metrics,
                    "data_source": getattr(post.data_source, "value", str(post.data_source)),
                }
            )
            if len(history) >= MAX_HISTORY_ROWS:
                break
        return history

    async def _lead_performance(self, organization_id: UUID, client_id: UUID) -> dict:
        """Lead counts by status. Counts of rows we hold — not conversion rates."""
        rows = await self.db.execute(
            select(Lead.status, func.count(Lead.id))
            .where(Lead.organization_id == organization_id, Lead.client_id == client_id)
            .group_by(Lead.status)
        )
        by_status: dict[str, int] = {}
        for status_value, count in rows.all():
            key = getattr(status_value, "value", str(status_value))
            by_status[key] = int(count or 0)
        total = sum(by_status.values())
        if not total:
            return {}

        score = await self.db.scalar(
            select(func.avg(Lead.lead_score)).where(
                Lead.organization_id == organization_id,
                Lead.client_id == client_id,
                Lead.lead_score.isnot(None),
            )
        )
        performance: dict = {"total_leads": total, "by_status": by_status}
        if score is not None:
            performance["average_lead_score"] = round(_num(score) or 0.0, 1)
        return performance

    async def _previous_strategies(self, organization_id: UUID, client_id: UUID) -> list[dict]:
        strategies = await self.db.scalars(
            select(Strategy)
            .where(Strategy.organization_id == organization_id, Strategy.client_id == client_id)
            .order_by(Strategy.created_at.desc())
            .limit(3)
        )
        return [
            {
                "title": strategy.title,
                "status": strategy.status,
                "summary": strategy.strategy_summary,
                "key_problems": strategy.key_problems or [],
                "opportunities": strategy.opportunities or [],
                "created_at": strategy.created_at.isoformat() if strategy.created_at else None,
            }
            for strategy in strategies
        ]

    # -- limitations ------------------------------------------------------

    def _limitations(self, context: ClientContext, history_is_demo: bool) -> list[str]:
        """
        The sentences an agent may use in place of a number it does not have.

        Written as prose rather than field names because they end up in the
        strategy document a human reads, and "No historical Meta campaign data
        available." is more useful to that human than "spend: null".
        """
        limitations: list[str] = []

        if not context.historical_campaign_performance:
            channels = ", ".join(context.primary_channels) or "any channel"
            limitations.append(
                f"No historical campaign performance data available for {channels}; "
                "cost per lead, CTR and ROAS cannot be stated."
            )
        elif history_is_demo:
            limitations.append(
                "Historical campaign performance available is demo data and must not be "
                "presented as this client's real results."
            )

        if not context.historical_content_performance:
            limitations.append(
                "No measured organic content performance available; creative "
                "recommendations are not grounded in past engagement."
            )
        if not context.lead_performance:
            limitations.append("No lead records exist for this client; lead quality is unknown.")
        if not context.previous_strategies:
            limitations.append("No previous strategy on file for this client.")

        missing = set(context.insufficient_data_fields or [])
        if {"impressions", "ctr"} & missing:
            limitations.append("No impression or click data recorded; CTR is unavailable.")
        if "revenue" in missing:
            limitations.append("No revenue recorded; ROAS and conversion value are unavailable.")
        if not context.website:
            limitations.append("No website on file; landing page quality was not assessed.")
        if not context.competitors:
            limitations.append("No competitors recorded; positioning is not benchmarked.")
        if context.monthly_budget is None:
            limitations.append("No stored monthly budget for this client.")

        # De-duplicated while preserving order: two sources can independently
        # notice the same gap and the reviewer should see it once.
        return list(dict.fromkeys(limitations))
