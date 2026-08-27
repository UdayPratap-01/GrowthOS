"""Milestone 4 — production gates, operator resolve, legacy EXECUTING, canary."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.automation.legacy_executing import LegacyRecoveryAction, recover_legacy_executing
from app.automation.manual_reconciliation import ManualResolution, manually_resolve_reconciliation
from app.automation.production_gates import evaluate_production_gates
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
from app.publishing.provider_errors import ReconciliationState
from app.publishing.provider_verification import verification_preflight, verify_meta_campaign_ops
from tests.test_closed_loop_optimization import _enable_opt, _mk_rec, _seed


async def _login(email: str, password: str = "pass") -> str:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        login = await client.post("/api/v1/auth/login", json={"email": email, "password": password})
        assert login.status_code == 200, login.text
        return login.json()["access_token"]


async def _seed_with_email(*, automation: bool = True, mode: AutonomyMode = AutonomyMode.assisted):
    email = f"m4-{uuid.uuid4().hex[:8]}@test.com"
    async with AsyncSessionLocal() as db:
        org = Organization(
            name=f"M4 Org {uuid.uuid4().hex[:6]}",
            slug=f"m4-{uuid.uuid4().hex[:8]}",
            demo_mode=False,
        )
        user = User(email=email, hashed_password=hash_password("pass"), full_name="M4 Owner")
        db.add_all([org, user])
        await db.flush()
        db.add(OrganizationMember(organization_id=org.id, user_id=user.id, role=MemberRole.owner))
        client = Client(organization_id=org.id, business_name="M4 Client", industry="saas")
        db.add(client)
        await db.flush()
        camp = Campaign(
            organization_id=org.id,
            client_id=client.id,
            name="M4 Camp",
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
                autonomy_mode=mode,
                automation_enabled=automation,
                require_approval_for_financial_actions=True,
                maximum_budget_increase_percentage=Decimal("20"),
                maximum_budget_decrease_percentage=Decimal("30"),
                maximum_campaign_budget=Decimal("500"),
                allowed_platforms=["meta"],
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
        return org.id, client.id, user.id, camp.id, email


async def _add_member(org_id, *, role=MemberRole.member) -> str:
    email = f"m4m-{uuid.uuid4().hex[:8]}@test.com"
    async with AsyncSessionLocal() as db:
        user = User(email=email, hashed_password=hash_password("pass"), full_name="Member")
        db.add(user)
        await db.flush()
        db.add(OrganizationMember(organization_id=org_id, user_id=user.id, role=role))
        await db.commit()
    return email


@pytest.mark.asyncio
async def test_kill_switch_blocks_autonomous_create(monkeypatch):
    org_id, client_id, user_id, _ = await _seed(
        automation=True, mode=AutonomyMode.autonomous, financial_approval=False
    )
    _enable_opt(
        monkeypatch,
        autonomous_execution_enabled=True,
        meta_autonomous_enabled=True,
        autonomous_kill_switch=True,
        autonomous_canary_org_ids=str(org_id),
        autonomous_canary_providers="meta",
        autonomous_canary_action_types="update_budget",
    )
    rec_id = await _mk_rec(org_id=org_id, client_id=client_id, percentage=8, direction="DECREASE")
    async with AsyncSessionLocal() as db:
        decision = await ClosedLoopOptimizer(db).evaluate_recommendation(
            organization_id=org_id, recommendation_id=rec_id, actor_user_id=user_id
        )
        await db.commit()
        actions = list((await db.scalars(select(AIAction).where(AIAction.organization_id == org_id))).all())
        rec = await db.get(PerformanceRecommendation, rec_id)
        audits = list(
            (
                await db.scalars(
                    select(AuditLog).where(
                        AuditLog.organization_id == org_id,
                        AuditLog.action == "autonomous.kill_switch_blocked",
                    )
                )
            ).all()
        )
    assert decision.decision == "BLOCKED"
    assert "KILL_SWITCH" in (decision.reason or "").upper() or any(
        c.get("code") == "AUTONOMOUS_KILL_SWITCH_ENABLED" for c in decision.policy_checks
    )
    assert actions == []
    assert rec is not None
    assert rec.status != PerformanceRecommendationStatus.rejected
    assert len(audits) >= 1


@pytest.mark.asyncio
async def test_canary_org_and_provider_gates(monkeypatch):
    org_id, client_id, user_id, _ = await _seed(
        automation=True, mode=AutonomyMode.autonomous, financial_approval=False
    )
    _enable_opt(
        monkeypatch,
        autonomous_execution_enabled=True,
        meta_autonomous_enabled=True,
        autonomous_canary_org_ids=str(uuid.uuid4()),  # different org
        autonomous_canary_providers="meta",
        autonomous_canary_action_types="update_budget",
    )
    rec_id = await _mk_rec(org_id=org_id, client_id=client_id, percentage=8, direction="DECREASE")
    async with AsyncSessionLocal() as db:
        decision = await ClosedLoopOptimizer(db).evaluate_recommendation(
            organization_id=org_id, recommendation_id=rec_id, actor_user_id=user_id
        )
        await db.commit()
        n = len(list((await db.scalars(select(AIAction).where(AIAction.organization_id == org_id))).all()))
    assert decision.decision == "BLOCKED"
    assert n == 0


@pytest.mark.asyncio
async def test_manual_resolution_unknown_paths(monkeypatch):
    _enable_opt(monkeypatch)
    org_id, client_id, user_id, camp_id = await _seed(automation=True, mode=AutonomyMode.assisted)
    async with AsyncSessionLocal() as db:
        action = AIAction(
            organization_id=org_id,
            client_id=client_id,
            action_type=AIActionType.pause_campaign,
            agent="test",
            platform="meta",
            target_id=str(camp_id),
            description="ambiguous",
            reason="timeout",
            status=AIActionStatus.failed,
            risk_level=RiskLevel.high,
            requires_approval=False,
            result={
                "reconciliation": {
                    "state": ReconciliationState.unknown.value,
                    "provider": "meta",
                    "operation": "pause",
                    "external_id": "ext-camp-1",
                    "ambiguous_error_code": "PROVIDER_TIMEOUT_AMBIGUOUS",
                    "ambiguous_since": datetime.now(timezone.utc).isoformat(),
                }
            },
            error="PROVIDER_STATE_UNKNOWN: timeout",
            payload={},
        )
        db.add(action)
        await db.commit()
        aid = action.id

        # KEEP_UNKNOWN
        kept = await manually_resolve_reconciliation(
            db,
            organization_id=org_id,
            action_id=aid,
            resolution=ManualResolution.keep_unknown,
            resolver_user_id=user_id,
            reason="Need more provider investigation",
        )
        await db.commit()
        assert kept.status == AIActionStatus.failed
        assert (kept.result or {})["reconciliation"]["state"] == "UNKNOWN"

        # Invalid transition from PENDING not allowed — reset to UNKNOWN then SUCCESS
        success = await manually_resolve_reconciliation(
            db,
            organization_id=org_id,
            action_id=aid,
            resolution=ManualResolution.confirm_success,
            resolver_user_id=user_id,
            reason="Verified paused in Ads Manager",
        )
        await db.commit()
        assert success.status == AIActionStatus.completed
        assert (success.result or {})["reconciliation"]["state"] == "CONFIRMED_SUCCESS"
        assert (success.result or {})["reconciliation"]["previous_ambiguous"]["ambiguous_error_code"]

        # Cannot resolve again
        with pytest.raises(ValueError):
            await manually_resolve_reconciliation(
                db,
                organization_id=org_id,
                action_id=aid,
                resolution=ManualResolution.confirm_not_applied,
                resolver_user_id=user_id,
                reason="too late",
            )


@pytest.mark.asyncio
async def test_manual_resolution_confirm_not_applied_and_rbac(monkeypatch):
    _enable_opt(monkeypatch)
    org_id, client_id, user_id, camp_id = await _seed(automation=True)
    org_b, _, user_b, _ = await _seed(automation=True)
    async with AsyncSessionLocal() as db:
        action = AIAction(
            organization_id=org_id,
            client_id=client_id,
            action_type=AIActionType.resume_campaign,
            agent="test",
            platform="meta",
            target_id=str(camp_id),
            description="ambiguous",
            reason="transport",
            status=AIActionStatus.failed,
            risk_level=RiskLevel.medium,
            requires_approval=False,
            result={
                "reconciliation": {
                    "state": "UNKNOWN",
                    "provider": "meta",
                    "operation": "resume",
                    "ambiguous_error_code": "PROVIDER_TRANSPORT_AMBIGUOUS",
                }
            },
            error="PROVIDER_STATE_UNKNOWN",
            payload={},
        )
        db.add(action)
        await db.commit()
        aid = action.id

        with pytest.raises(LookupError):
            await manually_resolve_reconciliation(
                db,
                organization_id=org_b,
                action_id=aid,
                resolution=ManualResolution.confirm_not_applied,
                resolver_user_id=user_b,
                reason="cross tenant",
            )

        updated = await manually_resolve_reconciliation(
            db,
            organization_id=org_id,
            action_id=aid,
            resolution=ManualResolution.confirm_not_applied,
            resolver_user_id=user_id,
            reason="Confirmed still active",
        )
        await db.commit()
        assert updated.status == AIActionStatus.failed
        assert (updated.result or {})["reconciliation"]["state"] == "CONFIRMED_NOT_APPLIED"
        audit = await db.scalar(
            select(AuditLog).where(
                AuditLog.organization_id == org_id,
                AuditLog.action == "ai_action.reconciliation_manually_resolved",
            )
        )
        assert audit is not None


@pytest.mark.asyncio
async def test_legacy_executing_recovery(monkeypatch):
    _enable_opt(monkeypatch)
    org_id, client_id, user_id, camp_id = await _seed(automation=True)
    async with AsyncSessionLocal() as db:
        action = AIAction(
            organization_id=org_id,
            client_id=client_id,
            action_type=AIActionType.pause_campaign,
            agent="test",
            platform="meta",
            target_id=str(camp_id),
            description="legacy stuck",
            reason="crash",
            status=AIActionStatus.executing,
            executing_at=None,
            risk_level=RiskLevel.high,
            requires_approval=False,
            payload={},
            result={},
        )
        db.add(action)
        await db.commit()
        aid = action.id

        left = await recover_legacy_executing(
            db,
            organization_id=org_id,
            action_id=aid,
            recovery=LegacyRecoveryAction.leave_executing,
            actor_user_id=user_id,
            reason="Investigating",
        )
        assert left.status == AIActionStatus.executing

        failed = await recover_legacy_executing(
            db,
            organization_id=org_id,
            action_id=aid,
            recovery=LegacyRecoveryAction.mark_failed,
            actor_user_id=user_id,
            reason="Confirmed never reached provider",
        )
        await db.commit()
        assert failed.status == AIActionStatus.failed
        audit = await db.scalar(
            select(AuditLog).where(
                AuditLog.organization_id == org_id,
                AuditLog.action == "legacy_action.recovered",
            )
        )
        assert audit is not None


@pytest.mark.asyncio
async def test_operator_apis_rbac_and_visibility(monkeypatch):
    _enable_opt(monkeypatch)
    org_id, client_id, user_id, camp_id, email = await _seed_with_email()
    token = await _login(email)
    member_email = await _add_member(org_id, role=MemberRole.member)
    member_token = await _login(member_email)
    async with AsyncSessionLocal() as db:
        action = AIAction(
            organization_id=org_id,
            client_id=client_id,
            action_type=AIActionType.pause_campaign,
            agent="closed_loop_optimizer",
            platform="meta",
            target_id=str(camp_id),
            description="amb",
            reason="t",
            status=AIActionStatus.failed,
            risk_level=RiskLevel.high,
            requires_approval=False,
            result={
                "reconciliation": {
                    "state": "PENDING",
                    "provider": "meta",
                    "operation": "pause",
                    "ambiguous_error_code": "PROVIDER_TIMEOUT_AMBIGUOUS",
                }
            },
            error="PROVIDER_TIMEOUT_AMBIGUOUS",
            payload={"recommendation_id": str(uuid.uuid4())},
        )
        db.add(action)
        await db.commit()
        aid = action.id

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        denied = await client.get("/api/v1/autopilot/operator/status")
        assert denied.status_code == 401

        status = await client.get(
            "/api/v1/autopilot/operator/status",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert status.status_code == 200
        body = status.json()
        assert body["autonomous_kill_switch"] is False
        assert body["optimization_enabled"] is True
        assert "providers" in body

        amb = await client.get(
            "/api/v1/autopilot/operator/actions/ambiguous",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert amb.status_code == 200
        assert any(i["action_id"] == str(aid) for i in amb.json()["items"])

        detail = await client.get(
            f"/api/v1/autopilot/operator/actions/{aid}/detail",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert detail.status_code == 200
        assert "lifecycle" in detail.json()

        bad = await client.post(
            f"/api/v1/autopilot/operator/actions/{aid}/resolve-reconciliation",
            headers={"Authorization": f"Bearer {member_token}"},
            json={"resolution": "KEEP_UNKNOWN", "reason": "nope member"},
        )
        assert bad.status_code == 403


@pytest.mark.asyncio
async def test_approval_path_not_blocked_by_kill_switch(monkeypatch):
    org_id, client_id, user_id, _ = await _seed(
        automation=True, mode=AutonomyMode.assisted, financial_approval=True
    )
    _enable_opt(monkeypatch, autonomous_kill_switch=True, autonomous_execution_enabled=False)
    rec_id = await _mk_rec(org_id=org_id, client_id=client_id, percentage=10, direction="DECREASE")
    async with AsyncSessionLocal() as db:
        decision = await ClosedLoopOptimizer(db).approve_recommendation(
            organization_id=org_id, recommendation_id=rec_id, actor_user_id=user_id
        )
        await db.commit()
        action = await db.scalar(select(AIAction).where(AIAction.organization_id == org_id))
    assert decision.decision in {"ACTION", "APPROVAL_REQUIRED"}
    assert action is not None


def test_provider_verification_disabled_by_default():
    settings = get_settings()
    ok, reason = verification_preflight(settings)
    assert ok is False
    assert reason is not None


@pytest.mark.asyncio
async def test_provider_verification_skips_without_confirm(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "provider_verification_enabled", True)
    monkeypatch.setattr(settings, "provider_verification_confirm", "")
    report = await verify_meta_campaign_ops()
    assert report.ran is False
    assert "CONFIRM" in (report.skipped_reason or "")


def test_production_gates_defaults_block_autonomous():
    settings = get_settings()
    autonomy = AutonomySettings(
        autonomy_mode=AutonomyMode.autonomous,
        automation_enabled=True,
        allowed_actions=[],
    )
    result = evaluate_production_gates(
        organization_id=uuid.uuid4(),
        client_id=None,
        platform="meta",
        action_type=AIActionType.update_budget,
        autonomy=autonomy,
        intent="autonomous",
        app_settings=settings,
    )
    assert result.allowed is False
