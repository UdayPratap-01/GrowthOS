from __future__ import annotations

from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.ai_ops import AIRecommendation
from app.models.client import Client
from app.models.enums import Priority, RecommendationStatus
from app.schemas.recommendation import RecommendationCreate, RecommendationOut, RecommendationStatusUpdate
from app.security.audit import write_audit
from app.services.analytics_service import AnalyticsService
from app.services.client_service import ClientService


class RecommendationService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db
        self.analytics = AnalyticsService(db)
        self.clients = ClientService(db)

    async def _with_names(self, rows: list[AIRecommendation]) -> list[RecommendationOut]:
        client_ids = {r.client_id for r in rows if r.client_id}
        names: dict[UUID, str] = {}
        if client_ids:
            result = await self.db.execute(select(Client).where(Client.id.in_(client_ids)))
            names = {c.id: c.business_name for c in result.scalars().all()}
        out: list[RecommendationOut] = []
        for r in rows:
            item = RecommendationOut.model_validate(r)
            item.client_name = names.get(r.client_id) if r.client_id else None
            out.append(item)
        return out

    async def list(
        self,
        organization_id: UUID,
        *,
        client_id: UUID | None = None,
        status_filter: RecommendationStatus | None = None,
    ) -> list[RecommendationOut]:
        stmt = select(AIRecommendation).where(AIRecommendation.organization_id == organization_id)
        if client_id:
            stmt = stmt.where(AIRecommendation.client_id == client_id)
        if status_filter:
            stmt = stmt.where(AIRecommendation.status == status_filter)
        stmt = stmt.order_by(AIRecommendation.created_at.desc())
        rows = list((await self.db.execute(stmt)).scalars().all())
        return await self._with_names(rows)

    async def create(self, organization_id: UUID, user_id: UUID, data: RecommendationCreate) -> RecommendationOut:
        if data.client_id:
            await self.clients.get_client(organization_id, data.client_id)
        row = AIRecommendation(organization_id=organization_id, **data.model_dump())
        self.db.add(row)
        await write_audit(
            self.db,
            action="recommendation.create",
            organization_id=organization_id,
            user_id=user_id,
            resource_type="ai_recommendation",
            resource_id=str(row.id),
        )
        await self.db.flush()
        await self.db.refresh(row)
        return (await self._with_names([row]))[0]

    async def update_status(
        self, organization_id: UUID, user_id: UUID, recommendation_id: UUID, data: RecommendationStatusUpdate
    ) -> RecommendationOut:
        row = await self.db.scalar(
            select(AIRecommendation).where(
                AIRecommendation.id == recommendation_id,
                AIRecommendation.organization_id == organization_id,
            )
        )
        if not row:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Recommendation not found")
        row.status = data.status
        await write_audit(
            self.db,
            action=f"recommendation.{data.status.value}",
            organization_id=organization_id,
            user_id=user_id,
            resource_type="ai_recommendation",
            resource_id=str(row.id),
        )
        await self.db.flush()
        await self.db.refresh(row)
        return (await self._with_names([row]))[0]

    async def generate_from_analytics(
        self, organization_id: UUID, user_id: UUID, *, client_id: UUID | None, demo_mode: bool
    ) -> list[RecommendationOut]:
        analytics = await self.analytics.get_analytics(
            organization_id, client_id=client_id, period_days=30, demo_mode=demo_mode
        )
        created: list[AIRecommendation] = []

        cpl_delta = analytics.deltas.get("cpl")
        if cpl_delta is not None and cpl_delta > 10:
            created.append(
                AIRecommendation(
                    organization_id=organization_id,
                    client_id=client_id,
                    title=f"CPL increased {cpl_delta}% vs prior period",
                    problem="Acquisition efficiency declined in the selected comparison window.",
                    evidence=(
                        f"Current CPL {analytics.current.cpl} vs previous {analytics.previous.cpl} "
                        f"(period {analytics.period_days}d, source={analytics.data_source})."
                    ),
                    recommendation="Review top spend campaigns and create 3 new creative variations for underperforming ads.",
                    priority=Priority.high,
                    expected_impact="Stabilize or reduce CPL within 14 days",
                    status=RecommendationStatus.pending,
                )
            )

        leads_delta = analytics.deltas.get("leads")
        if leads_delta is not None and leads_delta < -10:
            created.append(
                AIRecommendation(
                    organization_id=organization_id,
                    client_id=client_id,
                    title=f"Leads declined {abs(leads_delta)}% vs prior period",
                    problem="Lead volume fell versus the previous comparable window.",
                    evidence=(
                        f"Current leads {analytics.current.leads} vs previous {analytics.previous.leads} "
                        f"(source={analytics.data_source})."
                    ),
                    recommendation="Increase budget on highest-intent campaigns and tighten lead follow-up SLA.",
                    priority=Priority.high,
                    expected_impact="Recover lead volume toward prior baseline",
                    status=RecommendationStatus.pending,
                )
            )

        if analytics.current.ctr is not None and float(analytics.current.ctr) < 1.0:
            created.append(
                AIRecommendation(
                    organization_id=organization_id,
                    client_id=client_id,
                    title="CTR below 1% in current period",
                    problem="Click-through efficiency is weak on available traffic data.",
                    evidence=(
                        f"Current CTR {analytics.current.ctr}% from {analytics.current.impressions} impressions "
                        f"(source={analytics.data_source})."
                    ),
                    recommendation="Refresh hooks and primary creative; pause lowest-CTR placements.",
                    priority=Priority.medium,
                    expected_impact="Improve CTR and reduce wasted impressions",
                    status=RecommendationStatus.pending,
                )
            )

        if not created:
            if analytics.insufficient_data:
                created.append(
                    AIRecommendation(
                        organization_id=organization_id,
                        client_id=client_id,
                        title="Insufficient analytics for strong recommendations",
                        problem="Not enough comparable performance data to diagnose growth blockers confidently.",
                        evidence="; ".join(analytics.insufficient_data),
                        recommendation="Connect live integrations (Phase 3+) or ensure analytics cover the selected period.",
                        priority=Priority.medium,
                        expected_impact="Enable evidence-backed recommendations",
                        status=RecommendationStatus.pending,
                    )
                )
            else:
                created.append(
                    AIRecommendation(
                        organization_id=organization_id,
                        client_id=client_id,
                        title="Maintain content and conversion cadence",
                        problem="No severe efficiency regressions detected in available period comparison.",
                        evidence=(
                            f"Spend {analytics.current.spend}, leads {analytics.current.leads}, "
                            f"CTR {analytics.current.ctr}, CVR {analytics.current.conversion_rate} "
                            f"(source={analytics.data_source})."
                        ),
                        recommendation="Keep winning campaigns funded and ship a weekly content batch tied to primary offers.",
                        priority=Priority.low,
                        expected_impact="Protect pipeline consistency",
                        status=RecommendationStatus.pending,
                    )
                )

        for row in created:
            self.db.add(row)
        await write_audit(
            self.db,
            action="recommendation.generate",
            organization_id=organization_id,
            user_id=user_id,
            resource_type="ai_recommendation",
            details={"count": len(created), "client_id": str(client_id) if client_id else None},
        )
        await self.db.flush()
        for row in created:
            await self.db.refresh(row)
        return await self._with_names(created)
