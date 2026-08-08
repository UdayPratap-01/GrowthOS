from __future__ import annotations

from decimal import Decimal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.orchestrator import get_orchestrator
from app.models.automation import CampaignHealth, OptimizationEvent, OptimizationRule
from app.models.enums import AIActionType, HealthCategory, Priority, RiskLevel
from app.models.marketing import Campaign
from app.schemas.autopilot import (
    AIActionCreate,
    CampaignHealthOut,
    DecisionLoopRequest,
    DecisionLoopResult,
    OptimizationEventOut,
    OptimizationRuleIn,
    OptimizationRuleOut,
)
from app.services.action_service import ActionService
from app.services.analytics_service import AnalyticsService
from app.services.autonomy_service import AutonomyService
from app.services.client_service import ClientService


class OptimizationService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def list_rules(self, organization_id: UUID) -> list[OptimizationRuleOut]:
        rows = (
            await self.db.execute(
                select(OptimizationRule).where(OptimizationRule.organization_id == organization_id)
            )
        ).scalars().all()
        return [OptimizationRuleOut.model_validate(r) for r in rows]

    async def create_rule(self, organization_id: UUID, data: OptimizationRuleIn) -> OptimizationRuleOut:
        row = OptimizationRule(
            organization_id=organization_id,
            client_id=data.client_id,
            name=data.name,
            enabled=data.enabled,
            condition=data.condition,
            action_template=data.action_template,
            priority=data.priority,
        )
        self.db.add(row)
        await self.db.flush()
        await self.db.refresh(row)
        return OptimizationRuleOut.model_validate(row)

    async def list_events(self, organization_id: UUID, client_id: UUID | None = None) -> list[OptimizationEventOut]:
        stmt = (
            select(OptimizationEvent)
            .where(OptimizationEvent.organization_id == organization_id)
            .order_by(OptimizationEvent.created_at.desc())
            .limit(100)
        )
        if client_id:
            stmt = stmt.where(OptimizationEvent.client_id == client_id)
        rows = (await self.db.execute(stmt)).scalars().all()
        return [OptimizationEventOut.model_validate(r) for r in rows]

    async def analyze(self, organization, client_id: UUID, *, user_id: UUID) -> DecisionLoopResult:
        return await self.run_decision_loop(
            organization,
            DecisionLoopRequest(client_id=client_id, max_actions=5, max_iterations=1),
            user_id=user_id,
        )

    async def run_decision_loop(
        self, organization, data: DecisionLoopRequest, *, user_id: UUID
    ) -> DecisionLoopResult:
        """Controlled loop: collect → analyze → propose actions (never infinite)."""
        context = await ClientService(self.db).build_client_context(organization, data.client_id)
        analytics = await AnalyticsService(self.db).get_analytics(
            organization.id, client_id=data.client_id, period_days=30, demo_mode=organization.demo_mode
        )
        camps = (
            await self.db.execute(
                select(Campaign).where(
                    Campaign.organization_id == organization.id, Campaign.client_id == data.client_id
                )
            )
        ).scalars().all()
        campaign_payload = [
            {
                "id": str(c.id),
                "name": c.name,
                "platform": c.platform,
                "spend": float(c.spend or 0),
                "status": c.status,
                "metrics": c.metrics or {},
                "data_source": c.data_source.value if hasattr(c.data_source, "value") else str(c.data_source),
            }
            for c in camps
        ]
        analytics_summary = {
            "current": analytics.current.model_dump(mode="json"),
            "deltas": analytics.deltas,
            "insufficient_data": analytics.insufficient_data,
            "data_source": analytics.data_source,
        }

        # Deterministic health scoring from available metrics (no invented numbers)
        await self._score_campaigns(organization.id, data.client_id, camps)

        # Rule engine pass
        rule_events = await self._apply_rules(organization.id, data.client_id, analytics, camps)

        plan = await get_orchestrator().optimize(
            context,
            analytics_summary=analytics_summary,
            campaigns=campaign_payload,
        )

        settings = await AutonomyService(self.db).get_effective(organization.id, data.client_id)
        max_actions = min(data.max_actions, getattr(settings, "max_ai_actions_per_cycle", data.max_actions) or data.max_actions)
        max_iterations = min(data.max_iterations, getattr(settings, "max_ai_iterations", data.max_iterations) or data.max_iterations)
        if max_iterations < 1:
            max_iterations = 1

        actions_created = 0
        events_out: list[OptimizationEventOut] = []
        for event in rule_events:
            events_out.append(OptimizationEventOut.model_validate(event))

        for suggestion in plan.suggestions[: max_actions]:
            event = OptimizationEvent(
                organization_id=organization.id,
                client_id=data.client_id,
                problem=suggestion.problem,
                evidence=suggestion.evidence,
                recommendation=suggestion.recommendation,
                priority=self._priority(suggestion.priority),
                status="open",
            )
            self.db.add(event)
            await self.db.flush()

            action_type = self._map_action_type(suggestion.suggested_action_type)
            cost = (
                Decimal(str(suggestion.estimated_cost))
                if suggestion.estimated_cost is not None
                else (Decimal("50") if action_type.value in {"CREATE_CAMPAIGN", "CREATE_AD", "CREATE_AD_SET", "UPDATE_BUDGET"} else None)
            )
            try:
                created = await ActionService(self.db).create(
                    organization.id,
                    AIActionCreate(
                        action_type=action_type,
                        client_id=data.client_id,
                        agent="OptimizationAgent",
                        platform=suggestion.platform,
                        target_id=suggestion.target_id,
                        description=suggestion.recommendation,
                        reason=suggestion.problem,
                        evidence=suggestion.evidence,
                        expected_impact=suggestion.expected_impact,
                        estimated_cost=cost,
                        priority=self._priority(suggestion.priority),
                        risk_level=RiskLevel.medium,
                        payload={"source": "decision_loop", "insufficient_data": plan.insufficient_data},
                    ),
                    user_id=user_id,
                    organization=organization,
                )
                event.action_id = created.id
                actions_created += 1
            except Exception:
                # Keep optimization event even if action blocked by safety rules
                event.status = "blocked"
            await self.db.flush()
            events_out.append(OptimizationEventOut.model_validate(event))

        if not plan.suggestions and not rule_events:
            msg = "INSUFFICIENT DATA" if plan.insufficient_data or analytics.insufficient_data else "No optimization actions warranted."
        else:
            msg = f"Created {actions_created} structured actions from optimization loop."
        return DecisionLoopResult(actions_created=actions_created, events=events_out, message=msg)

    async def _score_campaigns(self, organization_id: UUID, client_id: UUID, camps: list[Campaign]) -> None:
        for c in camps:
            metrics = c.metrics or {}
            evidence: list[str] = []
            score = 50
            ctr = metrics.get("ctr")
            cpl = metrics.get("cpl")
            leads = metrics.get("leads") or metrics.get("conversions")
            if ctr is None and cpl is None and leads is None and float(c.spend or 0) == 0:
                evidence.append("INSUFFICIENT DATA for health scoring")
                category = HealthCategory.needs_attention
                score = 40
            else:
                if ctr is not None:
                    evidence.append(f"CTR={ctr}")
                    score += 10 if float(ctr) >= 1.5 else -10
                if cpl is not None:
                    evidence.append(f"CPL={cpl}")
                    score += 10 if float(cpl) <= 40 else -15
                if leads is not None:
                    evidence.append(f"leads={leads}")
                    score += 10 if int(leads) > 0 else -5
                evidence.append(f"spend={c.spend}")
                score = max(0, min(100, score))
                if score >= 85:
                    category = HealthCategory.excellent
                elif score >= 70:
                    category = HealthCategory.good
                elif score >= 50:
                    category = HealthCategory.needs_attention
                elif score >= 30:
                    category = HealthCategory.poor
                else:
                    category = HealthCategory.critical

            existing = await self.db.scalar(
                select(CampaignHealth).where(
                    CampaignHealth.organization_id == organization_id,
                    CampaignHealth.campaign_id == c.id,
                ).limit(1)
            )
            snapshot = {"spend": float(c.spend or 0), "metrics": metrics, "status": c.status}
            ds = c.data_source.value if hasattr(c.data_source, "value") else str(c.data_source)
            if existing:
                existing.score = score
                existing.category = category
                existing.evidence = evidence
                existing.metrics_snapshot = snapshot
                existing.data_source = ds
            else:
                self.db.add(
                    CampaignHealth(
                        organization_id=organization_id,
                        client_id=client_id,
                        campaign_id=c.id,
                        score=score,
                        category=category,
                        evidence=evidence,
                        metrics_snapshot=snapshot,
                        data_source=ds,
                    )
                )
        await self.db.flush()

    async def _apply_rules(
        self, organization_id: UUID, client_id: UUID, analytics, camps: list[Campaign]
    ) -> list[OptimizationEvent]:
        rules = (
            await self.db.execute(
                select(OptimizationRule).where(
                    OptimizationRule.organization_id == organization_id,
                    OptimizationRule.enabled.is_(True),
                )
            )
        ).scalars().all()
        events: list[OptimizationEvent] = []
        current_cpl = float(analytics.current.cpl) if analytics.current.cpl is not None else None
        spend = float(analytics.current.spend or 0)
        conversions = int(analytics.current.conversions or 0)

        for rule in rules:
            if rule.client_id and rule.client_id != client_id:
                continue
            cond = rule.condition or {}
            # Example: CPL > target_CPL * 1.30 AND spend > min AND conversions >= min
            target = cond.get("target_cpl")
            multiplier = float(cond.get("cpl_multiplier", 1.3))
            min_spend = float(cond.get("minimum_spend", 0))
            min_conv = int(cond.get("minimum_conversions", 0))
            if target is None or current_cpl is None:
                continue
            if current_cpl > float(target) * multiplier and spend > min_spend and conversions >= min_conv:
                event = OptimizationEvent(
                    organization_id=organization_id,
                    client_id=client_id,
                    rule_id=rule.id,
                    problem=f"CPL {current_cpl} exceeded target {target} × {multiplier}",
                    evidence=[
                        f"Current CPL = {current_cpl}",
                        f"Target CPL = {target}",
                        f"Spend = {spend}",
                        f"Conversions = {conversions}",
                        f"data_source = {analytics.data_source}",
                    ],
                    recommendation=(rule.action_template or {}).get("recommendation")
                    or "Create creative variations and review weakest campaign spend.",
                    priority=rule.priority,
                    status="open",
                )
                self.db.add(event)
                await self.db.flush()
                events.append(event)
        return events

    async def list_health(self, organization_id: UUID, client_id: UUID | None = None) -> list[CampaignHealthOut]:
        stmt = select(CampaignHealth).where(CampaignHealth.organization_id == organization_id)
        if client_id:
            stmt = stmt.where(CampaignHealth.client_id == client_id)
        rows = (await self.db.execute(stmt)).scalars().all()
        return [
            CampaignHealthOut(
                id=r.id,
                campaign_id=r.campaign_id,
                client_id=r.client_id,
                score=r.score,
                category=r.category.value if hasattr(r.category, "value") else str(r.category),
                evidence=r.evidence or [],
                metrics_snapshot=r.metrics_snapshot or {},
                data_source=r.data_source,
            )
            for r in rows
        ]

    def _priority(self, value: str | Priority) -> Priority:
        if isinstance(value, Priority):
            return value
        try:
            return Priority(str(value).lower())
        except Exception:
            return Priority.medium

    def _map_action_type(self, value: str | None) -> AIActionType:
        if not value:
            return AIActionType.optimize_campaign
        normalized = value.upper().replace(" ", "_")
        try:
            return AIActionType(normalized)
        except Exception:
            return AIActionType.optimize_campaign
