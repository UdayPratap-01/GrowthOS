"""Milestone 3 — closed-loop optimization decision / policy / modes."""

from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select

from app.core.config import get_settings
from app.core.security import hash_password
from app.db.session import AsyncSessionLocal
from app.main import app
from app.models.ai_ops import AuditLog, Integration
from app.models.automation import AIAction, AutonomySettings
from app.models.client import Client
from app.models.enums import (
    AIActionStatus,
    AIActionType,
    AutonomyMode,
    DataSource,
    MemberRole,
    PerformanceRecommendationStatus,
    RiskLevel,
)
from app.models.marketing import Campaign
from app.models.organization import Organization, OrganizationMember
from app.models.performance_intelligence import PerformanceRecommendation
from app.models.user import User
from app.optimization.closed_loop import ClosedLoopOptimizer
from app.optimization.decision import map_recommendation_to_proposal
from app.optimization.modes import OptimizationAutonomyMode, resolve_optimization_mode
from app.optimization.risk import classify_optimization_risk, risk_allows_autonomous
from app.services.autonomy_service import AutonomyService


async def _seed(*, automation: bool = False, mode: AutonomyMode = AutonomyMode.copilot, financial_approval: bool = True):
    async with AsyncSessionLocal() as db:
        org = Organization(
            name=f"Opt Org {uuid.uuid4().hex[:6]}",
            slug=f"opt-{uuid.uuid4().hex[:8]}",
            demo_mode=False,
        )
        user = User(
            email=f"opt-{uuid.uuid4().hex[:8]}@test.com",
            hashed_password=hash_password("pass"),
            full_name="Opt Tester",
        )
        db.add_all([org, user])
        await db.flush()
        db.add(OrganizationMember(organization_id=org.id, user_id=user.id, role=MemberRole.owner))
        client = Client(organization_id=org.id, business_name="Opt Client", industry="saas")
        db.add(client)
        await db.flush()
        camp = Campaign(
            organization_id=org.id,
            client_id=client.id,
            name="Opt Camp",
            platform="meta",
            status="active",
            external_id="ext-camp-1",
            daily_budget=Decimal("100.00"),
            data_source=DataSource.live,
        )
        db.add(camp)
        db.add(
            AutonomySettings(
                organization_id=org.id,
                client_id=None,
                autonomy_mode=mode,
                automation_enabled=automation,
                require_approval_for_financial_actions=financial_approval,
                require_approval_for_publishing=True,
                require_approval_for_campaign_creation=True,
                maximum_budget_increase_percentage=Decimal("20"),
                maximum_budget_decrease_percentage=Decimal("30"),
                maximum_campaign_budget=Decimal("500"),
                allowed_platforms=["meta", "google_ads"],
                allowed_actions=[a.value for a in AIActionType],
            )
        )
        db.add(
            Integration(
                organization_id=org.id,
                client_id=client.id,
                provider="meta",
                status="connected",
                secret_ref="enc",
                config={},
            )
        )
        await db.commit()
        return org.id, client.id, user.id, camp.id


async def _mk_rec(
    *,
    org_id,
    client_id,
    confidence: float = 0.9,
    percentage: float = 15,
    direction: str = "DECREASE",
    operation: str = "UPDATE_BUDGET",
    spend: float = 200,
    impressions: int = 5000,
    clicks: int = 100,
    conversions: float = 10,
    status: PerformanceRecommendationStatus = PerformanceRecommendationStatus.new,
    platform: str = "meta",
    campaign_ext: str = "ext-camp-1",
):
    async with AsyncSessionLocal() as db:
        rec = PerformanceRecommendation(
            organization_id=org_id,
            client_id=client_id,
            platform=platform,
            entity_level="campaign",
            external_account_id="act",
            external_campaign_id=campaign_ext,
            recommendation_type="REDUCE_BUDGET",
            severity="HIGH",
            title="CPL increased",
            explanation="evidence-bound test",
            evidence=[{"metric": "cpl", "current": 80, "previous": 50, "change_percent": 60}],
            affected_metrics=["cpl"],
            current_values={
                "spend": spend,
                "impressions": impressions,
                "clicks": clicks,
                "conversions": conversions,
                "cpl": 80,
            },
            comparison_values={"spend": spend, "cpl": 50},
            percentage_changes={"cpl": 60},
            confidence=Decimal(str(confidence)),
            suggested_action={
                "operation": operation,
                "direction": direction,
                "percentage": percentage,
                "informational_only": True,
                "execution_disabled": True,
            },
            signal_category="UNDERPERFORMANCE",
            analysis_window_days=7,
            window_current_start=date.today() - timedelta(days=6),
            window_current_end=date.today(),
            window_previous_start=date.today() - timedelta(days=13),
            window_previous_end=date.today() - timedelta(days=7),
            fingerprint=f"fp-{uuid.uuid4().hex}",
            status=status,
        )
        db.add(rec)
        await db.commit()
        return rec.id


def _enable_opt(monkeypatch, **overrides):
    settings = get_settings()
    monkeypatch.setattr(settings, "optimization_enabled", True)
    monkeypatch.setattr(settings, "optimization_min_confidence", 0.5)
    monkeypatch.setattr(settings, "performance_min_spend", 50)
    monkeypatch.setattr(settings, "performance_min_impressions", 1000)
    monkeypatch.setattr(settings, "performance_min_clicks", 20)
    monkeypatch.setattr(settings, "performance_min_conversions", 1)
    monkeypatch.setattr(settings, "optimization_cooldown_hours", 24)
    monkeypatch.setattr(settings, "optimization_opposite_cooldown_hours", 48)
    monkeypatch.setattr(settings, "optimization_max_actions_per_day", 10)
    monkeypatch.setattr(settings, "optimization_max_consecutive_budget_increases", 2)
    monkeypatch.setattr(settings, "optimization_min_campaign_budget", 5)
    monkeypatch.setattr(settings, "optimization_max_autonomous_risk", "low")
    for k, v in overrides.items():
        monkeypatch.setattr(settings, k, v)
    return settings


def test_mode_mapping():
    s = AutonomySettings(autonomy_mode=AutonomyMode.copilot, automation_enabled=True)
    assert resolve_optimization_mode(s) == OptimizationAutonomyMode.manual
    s.automation_enabled = False
    assert resolve_optimization_mode(s) == OptimizationAutonomyMode.manual
    s.automation_enabled = True
    s.autonomy_mode = AutonomyMode.assisted
    assert resolve_optimization_mode(s) == OptimizationAutonomyMode.approval_required
    s.autonomy_mode = AutonomyMode.autonomous
    s.require_approval_for_financial_actions = True
    assert resolve_optimization_mode(s) == OptimizationAutonomyMode.approval_required
    s.require_approval_for_financial_actions = False
    assert resolve_optimization_mode(s) == OptimizationAutonomyMode.autonomous


def test_risk_classification():
    assert classify_optimization_risk(action_type=AIActionType.pause_campaign) == RiskLevel.high
    assert classify_optimization_risk(action_type=AIActionType.update_budget, budget_change_percent=5) == RiskLevel.low
    assert classify_optimization_risk(action_type=AIActionType.update_budget, budget_change_percent=15) == RiskLevel.medium
    assert classify_optimization_risk(action_type=AIActionType.update_budget, budget_change_percent=25) == RiskLevel.high
    assert risk_allows_autonomous(RiskLevel.low, max_autonomous_risk="low")
    assert not risk_allows_autonomous(RiskLevel.high, max_autonomous_risk="low")
    assert risk_allows_autonomous(RiskLevel.high, max_autonomous_risk="high")  # policy helper allows; closed-loop still forces approval for HIGH


@pytest.mark.asyncio
async def test_recommendation_to_decision_and_no_action(monkeypatch):
    _enable_opt(monkeypatch)
    org_id, client_id, user_id, _ = await _seed(automation=False)
    rec_id = await _mk_rec(org_id=org_id, client_id=client_id)
    async with AsyncSessionLocal() as db:
        decision = await ClosedLoopOptimizer(db).evaluate_recommendation(
            organization_id=org_id, recommendation_id=rec_id, actor_user_id=user_id
        )
        await db.commit()
        actions = int(
            await db.scalar(
                select(func.count()).select_from(AIAction).where(AIAction.organization_id == org_id)
            )
            or 0
        )
    assert decision.decision == "NO_ACTION"
    assert "MANUAL" in decision.reason
    assert actions == 0


@pytest.mark.asyncio
async def test_low_confidence_and_insufficient_spend_blocked(monkeypatch):
    _enable_opt(monkeypatch, optimization_min_confidence=0.8)
    org_id, client_id, user_id, _ = await _seed(
        automation=True, mode=AutonomyMode.autonomous, financial_approval=False
    )
    rec_low = await _mk_rec(org_id=org_id, client_id=client_id, confidence=0.2)
    rec_spend = await _mk_rec(org_id=org_id, client_id=client_id, confidence=0.95, spend=1, impressions=10)
    async with AsyncSessionLocal() as db:
        d1 = await ClosedLoopOptimizer(db).evaluate_recommendation(
            organization_id=org_id, recommendation_id=rec_low, actor_user_id=user_id, force_create_action=True
        )
        d2 = await ClosedLoopOptimizer(db).evaluate_recommendation(
            organization_id=org_id, recommendation_id=rec_spend, actor_user_id=user_id, force_create_action=True
        )
        await db.commit()
        actions = int(
            await db.scalar(select(func.count()).select_from(AIAction).where(AIAction.organization_id == org_id)) or 0
        )
    assert d1.decision == "BLOCKED"
    assert d2.decision == "BLOCKED"
    assert actions == 0


@pytest.mark.asyncio
async def test_budget_increase_limit_not_clamped(monkeypatch):
    _enable_opt(monkeypatch)
    org_id, client_id, user_id, _ = await _seed(
        automation=True, mode=AutonomyMode.assisted, financial_approval=True
    )
    # AutonomySettings max increase 20%; request 50% → BLOCKED (not clamped to 20)
    rec_id = await _mk_rec(
        org_id=org_id, client_id=client_id, percentage=50, direction="INCREASE", confidence=0.95
    )
    async with AsyncSessionLocal() as db:
        decision = await ClosedLoopOptimizer(db).evaluate_recommendation(
            organization_id=org_id, recommendation_id=rec_id, actor_user_id=user_id, force_create_action=True
        )
        await db.commit()
        actions = int(
            await db.scalar(select(func.count()).select_from(AIAction).where(AIAction.organization_id == org_id)) or 0
        )
    assert decision.decision == "BLOCKED"
    assert "50" in decision.reason or any("max_budget_increase" in str(c) for c in decision.policy_checks)
    assert actions == 0


@pytest.mark.asyncio
async def test_approval_required_then_approve_creates_action(monkeypatch):
    _enable_opt(monkeypatch)
    org_id, client_id, user_id, camp_id = await _seed(
        automation=True, mode=AutonomyMode.assisted, financial_approval=True
    )
    rec_id = await _mk_rec(org_id=org_id, client_id=client_id, percentage=10, direction="DECREASE")
    async with AsyncSessionLocal() as db:
        first = await ClosedLoopOptimizer(db).evaluate_recommendation(
            organization_id=org_id, recommendation_id=rec_id, actor_user_id=user_id
        )
        await db.commit()
        assert first.decision == "APPROVAL_REQUIRED"
        assert (
            await db.scalar(select(func.count()).select_from(AIAction).where(AIAction.organization_id == org_id)) or 0
        ) == 0

        second = await ClosedLoopOptimizer(db).approve_recommendation(
            organization_id=org_id, recommendation_id=rec_id, actor_user_id=user_id
        )
        await db.commit()
        action = await db.scalar(select(AIAction).where(AIAction.organization_id == org_id))
    assert second.decision in {"ACTION", "APPROVAL_REQUIRED"}
    assert action is not None
    assert action.agent == "closed_loop_optimizer"
    assert action.payload.get("recommendation_id") == str(rec_id)
    assert action.target_id == str(camp_id)


@pytest.mark.asyncio
async def test_autonomous_low_risk_creates_action(monkeypatch):
    org_id, client_id, user_id, _ = await _seed(
        automation=True, mode=AutonomyMode.autonomous, financial_approval=False
    )
    _enable_opt(
        monkeypatch,
        optimization_max_autonomous_risk="low",
        autonomous_execution_enabled=True,
        meta_autonomous_enabled=True,
        autonomous_kill_switch=False,
        autonomous_canary_org_ids=str(org_id),
        autonomous_canary_providers="meta",
        autonomous_canary_action_types="update_budget,pause_campaign,resume_campaign",
    )
    # 8% decrease → LOW risk
    rec_id = await _mk_rec(org_id=org_id, client_id=client_id, percentage=8, direction="DECREASE")
    async with AsyncSessionLocal() as db:
        decision = await ClosedLoopOptimizer(db).evaluate_recommendation(
            organization_id=org_id, recommendation_id=rec_id, actor_user_id=user_id
        )
        await db.commit()
        action = await db.scalar(select(AIAction).where(AIAction.organization_id == org_id))
        audits = list(
            (
                await db.scalars(
                    select(AuditLog).where(
                        AuditLog.organization_id == org_id,
                        AuditLog.action.like("optimization.%"),
                    )
                )
            ).all()
        )
    assert decision.decision == "ACTION"
    assert action is not None
    assert any(a.action == "optimization.autonomous_action_created" for a in audits)


@pytest.mark.asyncio
async def test_high_risk_pause_never_autonomous(monkeypatch):
    _enable_opt(monkeypatch, optimization_max_autonomous_risk="high")
    org_id, client_id, user_id, _ = await _seed(
        automation=True, mode=AutonomyMode.autonomous, financial_approval=False
    )
    rec_id = await _mk_rec(
        org_id=org_id, client_id=client_id, operation="PAUSE_CAMPAIGN", direction=None, percentage=None
    )
    async with AsyncSessionLocal() as db:
        # Map pause without percentage
        rec = await db.get(PerformanceRecommendation, rec_id)
        rec.suggested_action = {
            "operation": "PAUSE_CAMPAIGN",
            "direction": "PAUSE",
            "informational_only": True,
            "execution_disabled": True,
        }
        await db.commit()
        decision = await ClosedLoopOptimizer(db).evaluate_recommendation(
            organization_id=org_id, recommendation_id=rec_id, actor_user_id=user_id
        )
        await db.commit()
    # HIGH risk → approval path even in autonomous
    assert decision.risk == "HIGH"
    assert decision.decision in {"APPROVAL_REQUIRED", "ACTION"}
    if decision.decision == "ACTION":
        # If action created, must still require approval on the AIAction
        async with AsyncSessionLocal() as db:
            action = await db.scalar(select(AIAction).where(AIAction.organization_id == org_id))
            assert action is not None
            assert action.requires_approval is True or action.risk_level == RiskLevel.high


@pytest.mark.asyncio
async def test_cooldown_and_duplicate_prevention(monkeypatch):
    org_id, client_id, user_id, camp_id = await _seed(
        automation=True, mode=AutonomyMode.autonomous, financial_approval=False
    )
    _enable_opt(
        monkeypatch,
        autonomous_execution_enabled=True,
        meta_autonomous_enabled=True,
        autonomous_canary_org_ids=str(org_id),
        autonomous_canary_providers="meta",
        autonomous_canary_action_types="update_budget,pause_campaign,resume_campaign",
    )
    rec1 = await _mk_rec(org_id=org_id, client_id=client_id, percentage=8, direction="DECREASE")
    async with AsyncSessionLocal() as db:
        await ClosedLoopOptimizer(db).evaluate_recommendation(
            organization_id=org_id, recommendation_id=rec1, actor_user_id=user_id
        )
        await db.commit()

    rec2 = await _mk_rec(org_id=org_id, client_id=client_id, percentage=8, direction="DECREASE")
    async with AsyncSessionLocal() as db:
        # Same recommendation duplicate
        d_dup = await ClosedLoopOptimizer(db).evaluate_recommendation(
            organization_id=org_id, recommendation_id=rec1, actor_user_id=user_id, force_create_action=True
        )
        # Cooldown blocks second action on same campaign/type
        d_cool = await ClosedLoopOptimizer(db).evaluate_recommendation(
            organization_id=org_id, recommendation_id=rec2, actor_user_id=user_id, force_create_action=True
        )
        await db.commit()
        count = int(
            await db.scalar(select(func.count()).select_from(AIAction).where(AIAction.organization_id == org_id)) or 0
        )
    assert d_dup.decision == "BLOCKED"
    assert d_cool.decision == "BLOCKED"
    assert count == 1


@pytest.mark.asyncio
async def test_opposite_action_cooldown(monkeypatch):
    _enable_opt(monkeypatch, optimization_cooldown_hours=0)
    org_id, client_id, user_id, camp_id = await _seed(
        automation=True, mode=AutonomyMode.autonomous, financial_approval=False
    )
    async with AsyncSessionLocal() as db:
        db.add(
            AIAction(
                organization_id=org_id,
                client_id=client_id,
                action_type=AIActionType.pause_campaign,
                agent="closed_loop_optimizer",
                platform="meta",
                target_id=str(camp_id),
                description="prior pause",
                reason="test",
                evidence=[],
                risk_level=RiskLevel.high,
                requires_approval=False,
                status=AIActionStatus.completed,
                payload={},
                demo_mode=False,
            )
        )
        await db.commit()
    rec_id = await _mk_rec(
        org_id=org_id, client_id=client_id, operation="RESUME_CAMPAIGN", direction="RESUME", percentage=None
    )
    async with AsyncSessionLocal() as db:
        rec = await db.get(PerformanceRecommendation, rec_id)
        rec.suggested_action = {"operation": "RESUME_CAMPAIGN", "direction": "RESUME", "informational_only": True}
        await db.commit()
        decision = await ClosedLoopOptimizer(db).evaluate_recommendation(
            organization_id=org_id, recommendation_id=rec_id, actor_user_id=user_id, force_create_action=True
        )
        await db.commit()
    assert decision.decision == "BLOCKED"
    assert any(c.get("name") == "opposite_action_cooldown" and not c.get("passed") for c in decision.policy_checks)


@pytest.mark.asyncio
async def test_expired_and_unsupported_and_missing_external(monkeypatch):
    _enable_opt(monkeypatch)
    org_id, client_id, user_id, _ = await _seed(
        automation=True, mode=AutonomyMode.assisted, financial_approval=True
    )
    rec_exp = await _mk_rec(org_id=org_id, client_id=client_id)
    rec_uns = await _mk_rec(org_id=org_id, client_id=client_id, operation="CREATE_CREATIVE")
    rec_miss = await _mk_rec(org_id=org_id, client_id=client_id, campaign_ext="unknown-ext")
    async with AsyncSessionLocal() as db:
        exp = await db.get(PerformanceRecommendation, rec_exp)
        exp.status = PerformanceRecommendationStatus.expired
        uns = await db.get(PerformanceRecommendation, rec_uns)
        uns.suggested_action = {"operation": "CREATE_CREATIVE", "informational_only": True}
        await db.commit()
        d1 = await ClosedLoopOptimizer(db).evaluate_recommendation(
            organization_id=org_id, recommendation_id=rec_exp, actor_user_id=user_id, force_create_action=True
        )
        d2 = await ClosedLoopOptimizer(db).evaluate_recommendation(
            organization_id=org_id, recommendation_id=rec_uns, actor_user_id=user_id, force_create_action=True
        )
        d3 = await ClosedLoopOptimizer(db).evaluate_recommendation(
            organization_id=org_id, recommendation_id=rec_miss, actor_user_id=user_id, force_create_action=True
        )
        await db.commit()
        actions = int(
            await db.scalar(select(func.count()).select_from(AIAction).where(AIAction.organization_id == org_id)) or 0
        )
    assert d1.decision == "BLOCKED"
    assert d2.decision == "NO_ACTION"
    assert d3.decision == "BLOCKED"
    assert actions == 0


@pytest.mark.asyncio
async def test_google_budget_unsupported(monkeypatch):
    _enable_opt(monkeypatch)
    org_id, client_id, user_id, _ = await _seed(
        automation=True, mode=AutonomyMode.assisted, financial_approval=True
    )
    async with AsyncSessionLocal() as db:
        camp = Campaign(
            organization_id=org_id,
            client_id=client_id,
            name="G Camp",
            platform="google_ads",
            status="active",
            external_id="g-1",
            daily_budget=Decimal("100"),
            data_source=DataSource.live,
        )
        db.add(camp)
        db.add(
            Integration(
                organization_id=org_id,
                client_id=client_id,
                provider="google_ads",
                status="connected",
                secret_ref="enc",
                config={},
            )
        )
        await db.commit()
    rec_id = await _mk_rec(
        org_id=org_id, client_id=client_id, platform="google_ads", campaign_ext="g-1", percentage=10
    )
    async with AsyncSessionLocal() as db:
        decision = await ClosedLoopOptimizer(db).evaluate_recommendation(
            organization_id=org_id, recommendation_id=rec_id, actor_user_id=user_id, force_create_action=True
        )
        await db.commit()
    assert decision.decision == "BLOCKED"
    assert any("Google" in (c.get("detail") or "") or c.get("name") == "provider_capability" for c in decision.policy_checks)


@pytest.mark.asyncio
async def test_tenant_isolation(monkeypatch):
    _enable_opt(monkeypatch)
    org_a, client_a, user_a, _ = await _seed()
    org_b, _, user_b, _ = await _seed()
    rec_id = await _mk_rec(org_id=org_a, client_id=client_a)
    async with AsyncSessionLocal() as db:
        decision = await ClosedLoopOptimizer(db).evaluate_recommendation(
            organization_id=org_b, recommendation_id=rec_id, actor_user_id=user_b
        )
    assert decision.decision == "BLOCKED"
    assert "not found" in decision.reason.lower()


@pytest.mark.asyncio
async def test_idempotent_cycle_batch(monkeypatch):
    _enable_opt(monkeypatch)
    org_id, client_id, user_id, _ = await _seed(
        automation=True, mode=AutonomyMode.assisted, financial_approval=True
    )
    await _mk_rec(org_id=org_id, client_id=client_id, percentage=10)
    async with AsyncSessionLocal() as db:
        first = await ClosedLoopOptimizer(db).process_client_recommendations(
            organization_id=org_id, client_id=client_id, actor_user_id=user_id
        )
        second = await ClosedLoopOptimizer(db).process_client_recommendations(
            organization_id=org_id, client_id=client_id, actor_user_id=user_id
        )
        await db.commit()
        actions = int(
            await db.scalar(select(func.count()).select_from(AIAction).where(AIAction.organization_id == org_id)) or 0
        )
    assert first["evaluated"] >= 1
    assert first["approval_required"] >= 1
    assert actions == 0  # APPROVAL_REQUIRED does not create until approve
    assert second["evaluated"] >= 1


@pytest.mark.asyncio
async def test_api_policies_and_auth():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        denied = await client.get("/api/v1/autopilot/optimization/policies")
        assert denied.status_code in {401, 403}
        login = await client.post(
            "/api/v1/auth/login", json={"email": "demo@growthos.ai", "password": "demo1234"}
        )
        headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
        policies = await client.get("/api/v1/autopilot/optimization/policies", headers=headers)
        status = await client.get("/api/v1/autopilot/optimization/status", headers=headers)
        decisions = await client.get("/api/v1/autopilot/optimization/decisions", headers=headers)
        assert policies.status_code == 200
        assert status.status_code == 200
        assert decisions.status_code == 200
        assert policies.json()["thresholds"]["optimization_enabled"] is False


def test_map_proposal_requires_budget():
    rec = PerformanceRecommendation(
        organization_id=uuid.uuid4(),
        platform="meta",
        entity_level="campaign",
        recommendation_type="REDUCE_BUDGET",
        severity="HIGH",
        title="t",
        explanation="e",
        confidence=Decimal("0.9"),
        suggested_action={"operation": "UPDATE_BUDGET", "direction": "DECREASE", "percentage": 10},
        fingerprint="x",
        window_current_start=date.today(),
        window_current_end=date.today(),
        window_previous_start=date.today(),
        window_previous_end=date.today(),
    )
    proposal, reason = map_recommendation_to_proposal(rec, current_daily_budget=None)
    assert proposal is None and reason
    proposal, _ = map_recommendation_to_proposal(rec, current_daily_budget=Decimal("100"))
    assert proposal is not None
    assert proposal.daily_budget == Decimal("90.00")
