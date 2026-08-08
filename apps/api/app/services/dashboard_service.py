from decimal import Decimal
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.mode import label_metrics_source
from app.models.ai_ops import AIRecommendation
from app.models.client import Client
from app.models.enums import ActionStatus, ClientStatus, RecommendationStatus
from app.models.leads import Lead
from app.models.marketing import AnalyticsDaily
from app.models.strategy import StrategyAction
from app.schemas.dashboard import (
    AIPriorityItem,
    ClientPerformanceCard,
    DashboardOut,
    KPIBlock,
    PendingApproval,
)


def _health_from_metrics(spend: Decimal, leads: int, cpl: Decimal | None, cvr: Decimal | None) -> int | None:
    """Deterministic score from available metrics only — never a fixed invent seed."""
    if not spend and not leads:
        return None
    score = 50
    evidence_points = 0
    if cpl is not None:
        evidence_points += 1
        if cpl <= 30:
            score += 20
        elif cpl <= 50:
            score += 10
        elif cpl <= 80:
            score -= 5
        else:
            score -= 15
    if cvr is not None:
        evidence_points += 1
        if cvr >= 5:
            score += 15
        elif cvr >= 2:
            score += 8
        else:
            score -= 5
    if leads > 0:
        evidence_points += 1
        score += min(10, leads // 5)
    if spend > 0:
        evidence_points += 1
    if evidence_points < 2:
        return None  # Insufficient data for a health score
    return max(0, min(100, score))


class DashboardService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def _row_sources(self, organization_id: UUID, client_id: UUID | None = None) -> set[str]:
        stmt = select(AnalyticsDaily.data_source).where(AnalyticsDaily.organization_id == organization_id).distinct()
        if client_id:
            stmt = stmt.where(AnalyticsDaily.client_id == client_id)
        rows = (await self.db.execute(stmt)).scalars().all()
        out: set[str] = set()
        for r in rows:
            out.add(r.value if hasattr(r, "value") else str(r))
        return out

    async def get_dashboard(self, organization_id: UUID, demo_mode: bool) -> DashboardOut:
        total_clients = await self.db.scalar(
            select(func.count()).select_from(Client).where(
                Client.organization_id == organization_id, Client.status == ClientStatus.active
            )
        )
        total_leads = await self.db.scalar(
            select(func.count()).select_from(Lead).where(Lead.organization_id == organization_id)
        )
        totals = await self.db.execute(
            select(
                func.coalesce(func.sum(AnalyticsDaily.spend), 0),
                func.coalesce(func.sum(AnalyticsDaily.revenue), 0),
                func.coalesce(func.sum(AnalyticsDaily.leads), 0),
                func.coalesce(func.sum(AnalyticsDaily.conversions), 0),
                func.coalesce(func.sum(AnalyticsDaily.clicks), 0),
            ).where(AnalyticsDaily.organization_id == organization_id)
        )
        spend, revenue, analytic_leads, conversions, clicks = totals.one()
        spend_d = Decimal(spend)
        revenue_d = Decimal(revenue)
        leads_for_cpl = int(analytic_leads or 0) or int(total_leads or 0)
        avg_cpl = (spend_d / Decimal(leads_for_cpl)).quantize(Decimal("0.01")) if leads_for_cpl and spend_d else None
        cvr = None
        if clicks and conversions:
            cvr = (Decimal(conversions) / Decimal(clicks) * 100).quantize(Decimal("0.01"))

        sources = await self._row_sources(organization_id)
        data_source = label_metrics_source(org_demo=demo_mode, row_sources=sources)
        health = _health_from_metrics(spend_d, leads_for_cpl, avg_cpl, cvr)

        kpis = KPIBlock(
            total_clients=int(total_clients or 0),
            total_leads=int(total_leads or 0),
            total_ad_spend=spend_d,
            estimated_revenue=revenue_d,
            average_cpl=avg_cpl,
            conversion_rate=cvr,
            marketing_health_score=health,
            data_source=data_source,
        )

        rec_rows = await self.db.execute(
            select(AIRecommendation)
            .where(
                AIRecommendation.organization_id == organization_id,
                AIRecommendation.status == RecommendationStatus.pending,
            )
            .order_by(AIRecommendation.created_at.desc())
            .limit(8)
        )
        recommendations = list(rec_rows.scalars().all())
        client_names = {
            c.id: c.business_name
            for c in (
                await self.db.execute(select(Client).where(Client.organization_id == organization_id))
            ).scalars().all()
        }

        ai_priorities = [
            AIPriorityItem(
                id=r.id,
                priority=r.priority.value,
                title=r.title,
                recommendation=r.recommendation,
                client_id=r.client_id,
                client_name=client_names.get(r.client_id) if r.client_id else None,
            )
            for r in recommendations
        ]

        clients = (
            await self.db.execute(
                select(Client).where(Client.organization_id == organization_id, Client.status == ClientStatus.active)
            )
        ).scalars().all()
        cards: list[ClientPerformanceCard] = []
        for client in clients:
            row = await self.db.execute(
                select(
                    func.coalesce(func.sum(AnalyticsDaily.spend), 0),
                    func.coalesce(func.sum(AnalyticsDaily.leads), 0),
                    func.coalesce(func.sum(AnalyticsDaily.clicks), 0),
                    func.coalesce(func.sum(AnalyticsDaily.conversions), 0),
                ).where(
                    AnalyticsDaily.organization_id == organization_id,
                    AnalyticsDaily.client_id == client.id,
                )
            )
            c_spend, c_leads, c_clicks, c_conv = row.one()
            c_spend_d = Decimal(c_spend)
            c_leads_i = int(c_leads or 0)
            cpl = (c_spend_d / Decimal(c_leads_i)).quantize(Decimal("0.01")) if c_leads_i and c_spend_d else None
            client_cvr = None
            if c_clicks and c_conv:
                client_cvr = (Decimal(c_conv) / Decimal(c_clicks) * 100).quantize(Decimal("0.01"))
            client_sources = await self._row_sources(organization_id, client.id)
            cards.append(
                ClientPerformanceCard(
                    client_id=client.id,
                    business_name=client.business_name,
                    industry=client.industry,
                    spend=c_spend_d,
                    leads=c_leads_i,
                    cpl=cpl,
                    health_score=_health_from_metrics(c_spend_d, c_leads_i, cpl, client_cvr),
                    data_source=label_metrics_source(org_demo=demo_mode, row_sources=client_sources),
                )
            )

        pending_actions = (
            await self.db.execute(
                select(StrategyAction)
                .options(selectinload(StrategyAction.strategy))
                .where(
                    StrategyAction.organization_id == organization_id,
                    StrategyAction.status == ActionStatus.pending,
                )
                .limit(10)
            )
        ).scalars().all()
        approvals = [
            PendingApproval(
                id=a.id,
                type="strategy_action",
                title=a.action,
                client_id=a.client_id,
                client_name=client_names.get(a.client_id, "Unknown"),
                priority=a.priority.value,
            )
            for a in pending_actions
        ]

        return DashboardOut(
            kpis=kpis,
            ai_priorities=ai_priorities,
            client_performance=cards,
            recent_recommendations=ai_priorities[:5],
            pending_approvals=approvals,
            demo_mode=demo_mode,
        )
