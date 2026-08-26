from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.orchestrator import get_orchestrator
from app.automation.rule_engine import evaluate_rule_conditions
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
        failures = 0
        cycle_started = datetime.now(timezone.utc)

        for event in rule_events:
            events_out.append(OptimizationEventOut.model_validate(event))
            spawned = await self._maybe_spawn_rule_action(
                organization, data.client_id, event, user_id=user_id, settings=settings
            )
            if spawned:
                actions_created += 1

        for iteration in range(max_iterations):
            if actions_created >= max_actions:
                break
            if failures >= getattr(settings, "max_failures_per_cycle", 3):
                break
            elapsed = (datetime.now(timezone.utc) - cycle_started).total_seconds()
            if elapsed > getattr(settings, "max_execution_time", 300):
                break

            suggestions = plan.suggestions if iteration == 0 else []
            for suggestion in suggestions[: max(0, max_actions - actions_created)]:
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
                    failures += 1
                    event.status = "blocked"
                await self.db.flush()
                events_out.append(OptimizationEventOut.model_validate(event))
            if iteration > 0:
                break  # Additional iterations reserved for future scheduled re-analysis

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
            metrics = {
                "cpl": current_cpl,
                "spend": spend,
                "conversions": conversions,
                "leads": conversions,
                "ctr": float(analytics.current.ctr) if analytics.current.ctr is not None else None,
            }
            conditions = cond.get("conditions") if isinstance(cond.get("conditions"), list) else cond
            match = evaluate_rule_conditions(conditions, metrics)
            if not match.matched:
                continue
            event = OptimizationEvent(
                organization_id=organization_id,
                client_id=client_id,
                rule_id=rule.id,
                problem=match.reason or f"Rule {rule.name} triggered",
                evidence=[
                    match.reason or "",
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

    async def _maybe_spawn_rule_action(self, organization, client_id: UUID, event: OptimizationEvent, *, user_id: UUID, settings) -> bool:
        template = {}
        if event.rule_id:
            rule = await self.db.get(OptimizationRule, event.rule_id)
            template = (rule.action_template or {}) if rule else {}
        action_type_raw = template.get("action_type") or "GENERATE_CREATIVE_VARIATIONS"
        try:
            action_type = AIActionType(action_type_raw)
        except ValueError:
            action_type = AIActionType.generate_creative_variations
        try:
            created = await ActionService(self.db).create(
                organization.id,
                AIActionCreate(
                    action_type=action_type,
                    client_id=client_id,
                    agent="OptimizationRuleEngine",
                    platform=template.get("platform"),
                    description=event.recommendation,
                    reason=event.problem,
                    evidence=event.evidence,
                    estimated_cost=Decimal(str(template["estimated_cost"])) if template.get("estimated_cost") else None,
                    priority=event.priority,
                    risk_level=RiskLevel.medium,
                    payload={"source": "optimization_rule", "optimization_event_id": str(event.id), **template.get("payload", {})},
                ),
                user_id=user_id,
                organization=organization,
            )
            event.action_id = created.id
            await self.db.flush()
            return True
        except Exception:
            event.status = "blocked"
            await self.db.flush()
            return False

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

    async def health_narrative(self, organization, client_id: UUID) -> dict:
        """
        A written summary of campaign health, built on top of scores that were
        already computed arithmetically in `_score_campaigns`.

        This is the role `MonitoringAgent` should have and the reason it was
        never called: it is a *campaign* analyst, not infrastructure monitoring
        — that is `app/observability/metrics.py`. Asking a language model to
        produce a health *score* would contradict "never invent metrics", so the
        numbers stay deterministic and the model only explains them. If the
        provider fails, the scores are still returned and the narrative is
        reported as unavailable rather than fabricated.
        """
        health = await self.list_health(organization.id, client_id)
        if not health:
            return {
                "overview": "No campaign health has been computed yet. Run an analysis first.",
                "health": [],
                "alerts": [],
                "insufficient_data": ["No scored campaigns for this client."],
                "narrative_available": False,
            }

        context = await ClientService(self.db).build_client_context(organization, client_id)
        payload = [
            {
                "campaign_id": str(row.campaign_id),
                "score": row.score,
                "category": row.category,
                "evidence": row.evidence,
            }
            for row in health
        ]

        try:
            report = await get_orchestrator().monitor(
                context,
                analytics_summary={"scored_campaigns": len(health)},
                campaigns=payload,
            )
        except Exception:
            # The scores are real and useful on their own; a provider outage
            # must not make a working feature look broken.
            return {
                "overview": "Narrative summary unavailable — the AI provider could not be reached.",
                "health": payload,
                "alerts": [],
                "insufficient_data": [],
                "narrative_available": False,
            }

        return {
            "overview": report.overview,
            # Deliberately our scores, not the model's: the narrative explains
            # the numbers, it does not get to change them.
            "health": payload,
            "alerts": report.alerts,
            "insufficient_data": report.insufficient_data,
            "narrative_available": True,
        }

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
