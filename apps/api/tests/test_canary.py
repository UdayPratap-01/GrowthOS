"""Milestone 5 Phase 2 — controlled live provider canary (mocked providers)."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.automation.canary import (
    CANARY_AGENT,
    CANARY_CONFIRM_PHRASE,
    canary_dry_run,
    canary_execute,
    evaluate_canary_gate,
    require_canary_confirm,
)
from app.core.config import get_settings
from app.core.security import hash_password
from app.db.session import AsyncSessionLocal
from app.main import app
from app.models.ai_ops import AuditLog, Integration
from app.models.automation import AIAction, AutonomySettings
from app.models.client import Client
from app.models.enums import AIActionStatus, AIActionType, AutonomyMode, DataSource, MemberRole, RiskLevel
from app.models.marketing import Campaign
from app.models.organization import Organization, OrganizationMember
from app.models.user import User
from app.publishing.ads_reconciliation import ReconciliationOutcome, ReconciliationResult
from app.publishing.provider_errors import ReconciliationState
from app.security.secrets import get_secret_store


def _fresh_verification(*, account_id: str = "act_111", campaign_id: str = "camp-canary-1") -> dict[str, Any]:
    return {
        "status": "VERIFIED",
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "safe_for_read": True,
        "safe_for_mutation": False,
        "account": {"id": account_id, "name": "Test"},
        "canary_resources": {
            "ad_account": {"id": account_id},
            "campaigns": [{"id": campaign_id, "name": "Canary", "status": "ACTIVE"}],
            "supported_capabilities": ["pause_campaign", "resume_campaign"],
        },
        "capabilities": [{"name": "READ_ACCOUNT", "status": "VERIFIED"}],
    }


async def _login(email: str, password: str = "pass") -> str:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        login = await client.post("/api/v1/auth/login", json={"email": email, "password": password})
        assert login.status_code == 200, login.text
        return login.json()["access_token"]


async def _seed(
    *,
    role: MemberRole = MemberRole.owner,
    connect_meta: bool = True,
    connect_google: bool = False,
    automation: bool = True,
    verification: dict[str, Any] | None = None,
    platform: str = "meta",
    external_id: str = "camp-canary-1",
    metrics: dict[str, Any] | None = None,
):
    email = f"m5p2-{uuid.uuid4().hex[:8]}@test.com"
    async with AsyncSessionLocal() as db:
        org = Organization(name=f"Canary {uuid.uuid4().hex[:6]}", slug=f"c-{uuid.uuid4().hex[:8]}", demo_mode=False)
        user = User(email=email, hashed_password=hash_password("pass"), full_name="Canary")
        db.add_all([org, user])
        await db.flush()
        db.add(OrganizationMember(organization_id=org.id, user_id=user.id, role=role))
        client = Client(organization_id=org.id, business_name="Canary Client", industry="saas")
        db.add(client)
        await db.flush()
        met = metrics or {"ad_account_id": "act_111", "account_id": "act_111"}
        if platform in {"google", "google_ads"}:
            met = metrics or {"customer_id": "999", "external_campaign_id": external_id}
        camp = Campaign(
            organization_id=org.id,
            client_id=client.id,
            name="Canary Camp",
            platform=platform if platform != "google_ads" else "google",
            status="active",
            external_id=external_id,
            daily_budget=Decimal("50.00"),
            data_source=DataSource.live,
            metrics=met,
        )
        db.add(camp)
        db.add(
            AutonomySettings(
                organization_id=org.id,
                autonomy_mode=AutonomyMode.assisted,
                automation_enabled=automation,
                require_approval_for_financial_actions=True,
                maximum_budget_increase_percentage=Decimal("20"),
                maximum_budget_decrease_percentage=Decimal("30"),
                maximum_campaign_budget=Decimal("500"),
                allowed_platforms=["meta", "google", "google_ads"],
                allowed_actions=[a.value for a in AIActionType],
            )
        )
        if connect_meta:
            ref = get_secret_store().store(json.dumps({"access_token": "meta-token-secret"}))
            cfg: dict[str, Any] = {
                "account_label": "Act 1",
                "external_account_id": "act_111",
            }
            if verification is not None:
                cfg["last_verification"] = verification
            else:
                cfg["last_verification"] = _fresh_verification()
            db.add(
                Integration(
                    organization_id=org.id,
                    client_id=client.id,
                    provider="meta",
                    status="connected",
                    secret_ref=ref,
                    config=cfg,
                )
            )
        if connect_google:
            ref = get_secret_store().store(
                json.dumps({"access_token": "google-token-secret", "refresh_token": "refresh-secret"})
            )
            cfg = {
                "account_label": "G Ads",
                "external_account_id": "999",
                "customer_id": "999",
                "last_verification": verification
                or {
                    **_fresh_verification(account_id="999", campaign_id=external_id),
                    "status": "VERIFIED",
                    "canary_resources": {
                        "customer": {"id": "999"},
                        "campaigns": [{"id": external_id, "name": "G", "status": "ENABLED"}],
                        "supported_capabilities": ["pause_campaign", "resume_campaign"],
                    },
                },
            }
            db.add(
                Integration(
                    organization_id=org.id,
                    client_id=client.id,
                    provider="google_ads",
                    status="connected",
                    secret_ref=ref,
                    config=cfg,
                )
            )
        await db.commit()
        return org.id, client.id, user.id, camp.id, email


def _enable_canary(monkeypatch, org_id, *, provider="meta", action="pause_campaign", env="test"):
    settings = get_settings()
    monkeypatch.setattr(settings, "canary_enabled", True)
    monkeypatch.setattr(settings, "canary_allowed_org_ids", str(org_id))
    monkeypatch.setattr(settings, "canary_allowed_providers", provider if provider != "google" else "google_ads")
    monkeypatch.setattr(settings, "canary_allowed_actions", action)
    monkeypatch.setattr(settings, "canary_allowed_environments", env)
    monkeypatch.setattr(settings, "canary_allowed_meta_ad_accounts", "act_111,111")
    monkeypatch.setattr(settings, "canary_allowed_meta_campaigns", "camp-canary-1")
    monkeypatch.setattr(settings, "canary_allowed_google_customers", "999")
    monkeypatch.setattr(settings, "canary_allowed_google_campaigns", "camp-canary-1")
    monkeypatch.setattr(settings, "canary_max_actions_per_day", 5)
    monkeypatch.setattr(settings, "canary_max_actions_per_run", 1)
    monkeypatch.setattr(settings, "canary_max_spend_impact", 0.0)
    monkeypatch.setattr(settings, "provider_verification_max_age_hours", 24)
    monkeypatch.setattr(settings, "autonomous_kill_switch", False)
    monkeypatch.setattr(settings, "environment", env)
    monkeypatch.setattr(settings, "meta_app_id", "app")
    monkeypatch.setattr(settings, "meta_app_secret", "secret")
    monkeypatch.setattr(settings, "google_client_id", "cid")
    monkeypatch.setattr(settings, "google_client_secret", "csec")
    monkeypatch.setattr(settings, "google_ads_developer_token", "devtok")
    return settings


# ---- Confirm phrase ----------------------------------------------------------


def test_canary_confirm_phrase():
    ok, _ = require_canary_confirm(CANARY_CONFIRM_PHRASE)
    assert ok
    bad, reason = require_canary_confirm("I_CONFIRM_READ_ONLY_PROVIDER_VERIFICATION")
    assert not bad
    assert reason


# ---- Gate blocks -------------------------------------------------------------


@pytest.mark.asyncio
async def test_canary_disabled(monkeypatch):
    org_id, client_id, _, camp_id, _ = await _seed()
    settings = get_settings()
    monkeypatch.setattr(settings, "canary_enabled", False)
    async with AsyncSessionLocal() as db:
        gate = await evaluate_canary_gate(
            db,
            organization_id=org_id,
            provider="meta",
            action_type="pause_campaign",
            campaign_id=camp_id,
            client_id=client_id,
        )
    assert gate.allowed is False
    assert gate.blocked_code == "BLOCKED_CANARY_DISABLED"
    assert gate.readiness == "DISABLED"


@pytest.mark.asyncio
async def test_empty_allowlist_blocks(monkeypatch):
    org_id, client_id, _, camp_id, _ = await _seed()
    settings = get_settings()
    monkeypatch.setattr(settings, "canary_enabled", True)
    monkeypatch.setattr(settings, "canary_allowed_org_ids", "")
    monkeypatch.setattr(settings, "canary_allowed_environments", "test")
    monkeypatch.setattr(settings, "environment", "test")
    async with AsyncSessionLocal() as db:
        gate = await evaluate_canary_gate(
            db,
            organization_id=org_id,
            provider="meta",
            action_type="pause_campaign",
            campaign_id=camp_id,
            client_id=client_id,
        )
    assert gate.allowed is False
    assert gate.blocked_code == "BLOCKED_ORG_NOT_ALLOWLISTED"
    assert gate.readiness == "NOT_CONFIGURED"


@pytest.mark.asyncio
async def test_org_provider_account_campaign_action_allowlists(monkeypatch):
    org_id, client_id, _, camp_id, _ = await _seed()
    other = uuid.uuid4()
    settings = _enable_canary(monkeypatch, other)
    async with AsyncSessionLocal() as db:
        gate = await evaluate_canary_gate(
            db,
            organization_id=org_id,
            provider="meta",
            action_type="pause_campaign",
            campaign_id=camp_id,
            client_id=client_id,
        )
    assert gate.blocked_code == "BLOCKED_ORG_NOT_ALLOWLISTED"

    monkeypatch.setattr(settings, "canary_allowed_org_ids", str(org_id))
    monkeypatch.setattr(settings, "canary_allowed_providers", "google_ads")
    async with AsyncSessionLocal() as db:
        gate = await evaluate_canary_gate(
            db,
            organization_id=org_id,
            provider="meta",
            action_type="pause_campaign",
            campaign_id=camp_id,
            client_id=client_id,
        )
    assert gate.blocked_code == "BLOCKED_PROVIDER_DISABLED"

    _enable_canary(monkeypatch, org_id)
    settings = get_settings()
    monkeypatch.setattr(settings, "canary_allowed_actions", "resume_campaign")
    async with AsyncSessionLocal() as db:
        gate = await evaluate_canary_gate(
            db,
            organization_id=org_id,
            provider="meta",
            action_type="pause_campaign",
            campaign_id=camp_id,
            client_id=client_id,
        )
    assert gate.blocked_code == "BLOCKED_ACTION_NOT_ALLOWLISTED"

    _enable_canary(monkeypatch, org_id)
    settings = get_settings()
    monkeypatch.setattr(settings, "canary_allowed_meta_ad_accounts", "act_999")
    async with AsyncSessionLocal() as db:
        gate = await evaluate_canary_gate(
            db,
            organization_id=org_id,
            provider="meta",
            action_type="pause_campaign",
            campaign_id=camp_id,
            client_id=client_id,
        )
    assert gate.blocked_code == "BLOCKED_ACCOUNT_NOT_ALLOWLISTED"

    _enable_canary(monkeypatch, org_id)
    settings = get_settings()
    monkeypatch.setattr(settings, "canary_allowed_meta_campaigns", "other-camp")
    async with AsyncSessionLocal() as db:
        gate = await evaluate_canary_gate(
            db,
            organization_id=org_id,
            provider="meta",
            action_type="pause_campaign",
            campaign_id=camp_id,
            client_id=client_id,
        )
    assert gate.blocked_code == "BLOCKED_CAMPAIGN_NOT_ALLOWLISTED"


@pytest.mark.asyncio
async def test_provider_not_connected_and_not_verified(monkeypatch):
    org_id, client_id, _, camp_id, _ = await _seed(connect_meta=False)
    _enable_canary(monkeypatch, org_id)
    async with AsyncSessionLocal() as db:
        gate = await evaluate_canary_gate(
            db,
            organization_id=org_id,
            provider="meta",
            action_type="pause_campaign",
            campaign_id=camp_id,
            client_id=client_id,
        )
    assert gate.blocked_code in {"BLOCKED_PROVIDER_DISABLED", "BLOCKED_CAPABILITY", "BLOCKED_PROVIDER_NOT_VERIFIED"}

    org_id, client_id, _, camp_id, _ = await _seed(verification={"status": "FAILED", "checked_at": datetime.now(timezone.utc).isoformat()})
    _enable_canary(monkeypatch, org_id)
    async with AsyncSessionLocal() as db:
        gate = await evaluate_canary_gate(
            db,
            organization_id=org_id,
            provider="meta",
            action_type="pause_campaign",
            campaign_id=camp_id,
            client_id=client_id,
        )
    assert gate.blocked_code == "BLOCKED_PROVIDER_NOT_VERIFIED"


@pytest.mark.asyncio
async def test_stale_verification(monkeypatch):
    stale = _fresh_verification()
    stale["checked_at"] = (datetime.now(timezone.utc) - timedelta(hours=48)).isoformat()
    org_id, client_id, _, camp_id, _ = await _seed(verification=stale)
    _enable_canary(monkeypatch, org_id)
    settings = get_settings()
    monkeypatch.setattr(settings, "provider_verification_max_age_hours", 24)
    async with AsyncSessionLocal() as db:
        gate = await evaluate_canary_gate(
            db,
            organization_id=org_id,
            provider="meta",
            action_type="pause_campaign",
            campaign_id=camp_id,
            client_id=client_id,
        )
    assert gate.blocked_code == "BLOCKED_STALE_VERIFICATION"


@pytest.mark.asyncio
async def test_kill_switch_and_policy(monkeypatch):
    org_id, client_id, _, camp_id, _ = await _seed(automation=False)
    settings = _enable_canary(monkeypatch, org_id)
    monkeypatch.setattr(settings, "autonomous_kill_switch", True)
    async with AsyncSessionLocal() as db:
        gate = await evaluate_canary_gate(
            db,
            organization_id=org_id,
            provider="meta",
            action_type="pause_campaign",
            campaign_id=camp_id,
            client_id=client_id,
        )
    assert gate.blocked_code == "BLOCKED_KILL_SWITCH"

    monkeypatch.setattr(settings, "autonomous_kill_switch", False)
    async with AsyncSessionLocal() as db:
        gate = await evaluate_canary_gate(
            db,
            organization_id=org_id,
            provider="meta",
            action_type="pause_campaign",
            campaign_id=camp_id,
            client_id=client_id,
        )
    assert gate.blocked_code == "BLOCKED_POLICY"


@pytest.mark.asyncio
async def test_google_budget_capability_block(monkeypatch):
    org_id, client_id, _, camp_id, _ = await _seed(
        connect_meta=False,
        connect_google=True,
        platform="google_ads",
        external_id="camp-canary-1",
    )
    _enable_canary(monkeypatch, org_id, provider="google_ads", action="update_budget")
    async with AsyncSessionLocal() as db:
        gate = await evaluate_canary_gate(
            db,
            organization_id=org_id,
            provider="google_ads",
            action_type="update_budget",
            campaign_id=camp_id,
            client_id=client_id,
        )
    assert gate.allowed is False
    assert gate.blocked_code in {"BLOCKED_CAPABILITY", "BLOCKED_SPEND_LIMIT"}


@pytest.mark.asyncio
async def test_daily_limit_and_duplicates(monkeypatch):
    org_id, client_id, user_id, camp_id, _ = await _seed()
    settings = _enable_canary(monkeypatch, org_id)
    monkeypatch.setattr(settings, "canary_max_actions_per_day", 1)
    async with AsyncSessionLocal() as db:
        db.add(
            AIAction(
                organization_id=org_id,
                client_id=client_id,
                action_type=AIActionType.pause_campaign,
                agent=CANARY_AGENT,
                platform="meta",
                target_id=str(camp_id),
                description="prior",
                reason="prior",
                evidence=[],
                risk_level=RiskLevel.high,
                status=AIActionStatus.completed,
            )
        )
        await db.commit()
        gate = await evaluate_canary_gate(
            db,
            organization_id=org_id,
            provider="meta",
            action_type="pause_campaign",
            campaign_id=camp_id,
            client_id=client_id,
        )
    assert gate.blocked_code == "BLOCKED_DAILY_LIMIT"

    org_id, client_id, _, camp_id, _ = await _seed()
    _enable_canary(monkeypatch, org_id)
    async with AsyncSessionLocal() as db:
        db.add(
            AIAction(
                organization_id=org_id,
                client_id=client_id,
                action_type=AIActionType.pause_campaign,
                agent="other",
                platform="meta",
                target_id=str(camp_id),
                description="open",
                reason="open",
                evidence=[],
                risk_level=RiskLevel.high,
                status=AIActionStatus.pending,
            )
        )
        await db.commit()
        gate = await evaluate_canary_gate(
            db,
            organization_id=org_id,
            provider="meta",
            action_type="pause_campaign",
            campaign_id=camp_id,
            client_id=client_id,
        )
    assert gate.blocked_code == "BLOCKED_DUPLICATE"


@pytest.mark.asyncio
async def test_pending_executing_unknown_block(monkeypatch):
    for status in (AIActionStatus.pending, AIActionStatus.executing):
        org_id, client_id, _, camp_id, _ = await _seed()
        _enable_canary(monkeypatch, org_id)
        async with AsyncSessionLocal() as db:
            db.add(
                AIAction(
                    organization_id=org_id,
                    client_id=client_id,
                    action_type=AIActionType.pause_campaign,
                    agent="x",
                    platform="meta",
                    target_id=str(camp_id),
                    description="x",
                    reason="x",
                    evidence=[],
                    risk_level=RiskLevel.high,
                    status=status,
                    executing_at=datetime.now(timezone.utc) if status == AIActionStatus.executing else None,
                )
            )
            await db.commit()
            gate = await evaluate_canary_gate(
                db,
                organization_id=org_id,
                provider="meta",
                action_type="pause_campaign",
                campaign_id=camp_id,
                client_id=client_id,
            )
        assert gate.blocked_code == "BLOCKED_DUPLICATE"

    org_id, client_id, _, camp_id, _ = await _seed()
    _enable_canary(monkeypatch, org_id)
    async with AsyncSessionLocal() as db:
        db.add(
            AIAction(
                organization_id=org_id,
                client_id=client_id,
                action_type=AIActionType.pause_campaign,
                agent="x",
                platform="meta",
                target_id=str(camp_id),
                description="x",
                reason="x",
                evidence=[],
                risk_level=RiskLevel.high,
                status=AIActionStatus.failed,
                result={
                    "reconciliation": {
                        "state": ReconciliationState.unknown.value,
                        "ambiguous_error_code": "TIMEOUT",
                    }
                },
            )
        )
        await db.commit()
        gate = await evaluate_canary_gate(
            db,
            organization_id=org_id,
            provider="meta",
            action_type="pause_campaign",
            campaign_id=camp_id,
            client_id=client_id,
        )
    assert gate.blocked_code == "BLOCKED_RECONCILIATION"


# ---- Dry-run / execute -------------------------------------------------------


@pytest.mark.asyncio
async def test_dry_run_never_mutates(monkeypatch):
    org_id, client_id, user_id, camp_id, _ = await _seed()
    _enable_canary(monkeypatch, org_id)
    async with AsyncSessionLocal() as db:
        result = await canary_dry_run(
            db,
            organization_id=org_id,
            provider="meta",
            action_type="pause_campaign",
            campaign_id=camp_id,
            external_campaign_id=None,
            client_id=client_id,
            actor_user_id=user_id,
        )
        await db.commit()
        actions = list((await db.scalars(select(AIAction).where(AIAction.organization_id == org_id))).all())
        audits = list(
            (
                await db.scalars(
                    select(AuditLog).where(
                        AuditLog.organization_id == org_id,
                        AuditLog.action.in_(["canary.dry_run", "canary.blocked"]),
                    )
                )
            ).all()
        )
    assert result["mutation"] is False
    assert result["dry_run"] is True
    assert result["eligible"] is True
    assert actions == []
    assert audits
    dumped = json.dumps(result)
    assert "meta-token-secret" not in dumped
    assert "access_token" not in dumped


@pytest.mark.asyncio
async def test_execute_confirm_and_kill_switch(monkeypatch):
    org_id, client_id, user_id, camp_id, _ = await _seed()
    settings = _enable_canary(monkeypatch, org_id)
    async with AsyncSessionLocal() as db:
        bad = await canary_execute(
            db,
            organization_id=org_id,
            provider="meta",
            action_type="pause_campaign",
            campaign_id=camp_id,
            external_campaign_id=None,
            client_id=client_id,
            actor_user_id=user_id,
            confirm="WRONG",
        )
        await db.commit()
    assert bad["executed"] is False
    assert bad["blocked_code"] == "BLOCKED_INVALID_CONFIRM"

    monkeypatch.setattr(settings, "autonomous_kill_switch", True)
    async with AsyncSessionLocal() as db:
        blocked = await canary_execute(
            db,
            organization_id=org_id,
            provider="meta",
            action_type="pause_campaign",
            campaign_id=camp_id,
            external_campaign_id=None,
            client_id=client_id,
            actor_user_id=user_id,
            confirm=CANARY_CONFIRM_PHRASE,
        )
    assert blocked["blocked_code"] == "BLOCKED_KILL_SWITCH"


@pytest.mark.asyncio
async def test_meta_canary_execute_with_post_verify(monkeypatch):
    org_id, client_id, user_id, camp_id, _ = await _seed()
    _enable_canary(monkeypatch, org_id)

    mock_out = type(
        "Out",
        (),
        {
            "id": uuid.uuid4(),
            "status": AIActionStatus.completed,
            "model_dump": lambda self, mode="json": {"id": str(self.id), "status": "completed"},
        },
    )()

    async def fake_create(*args, **kwargs):
        return mock_out

    async def fake_approve(*args, **kwargs):
        return mock_out

    async def fake_get(*args, **kwargs):
        async with AsyncSessionLocal() as db:
            # Build a minimal action row in-memory object
            action = AIAction(
                organization_id=org_id,
                client_id=client_id,
                action_type=AIActionType.pause_campaign,
                agent=CANARY_AGENT,
                platform="meta",
                target_id=str(camp_id),
                description="canary",
                reason="canary",
                evidence=[],
                risk_level=RiskLevel.high,
                status=AIActionStatus.completed,
            )
            action.id = mock_out.id
            return action

    post = ReconciliationResult(
        outcome=ReconciliationOutcome.confirmed_success,
        message="PAUSED",
        provider="meta",
        operation="pause_campaign",
        observed_state={"status": "PAUSED"},
    )

    with (
        patch("app.automation.canary.ActionService.create", new=AsyncMock(side_effect=fake_create)),
        patch("app.automation.canary.ActionService.approve", new=AsyncMock(side_effect=fake_approve)),
        patch("app.automation.canary.ActionService.get", new=AsyncMock(side_effect=fake_get)),
        patch(
            "app.automation.canary.AdsReconciler.reconcile",
            new=AsyncMock(return_value=post),
        ),
    ):
        async with AsyncSessionLocal() as db:
            result = await canary_execute(
                db,
                organization_id=org_id,
                provider="meta",
                action_type="pause_campaign",
                campaign_id=camp_id,
                external_campaign_id=None,
                client_id=client_id,
                actor_user_id=user_id,
                confirm=CANARY_CONFIRM_PHRASE,
            )
            await db.commit()
            audits = list(
                (
                    await db.scalars(
                        select(AuditLog).where(
                            AuditLog.organization_id == org_id,
                            AuditLog.action.like("canary.%"),
                        )
                    )
                ).all()
            )
    assert result["executed"] is True
    assert result["mutation"] is True
    assert result["post_verification"]["outcome"] == "CONFIRMED_SUCCESS"
    assert any(a.action == "canary.execution_requested" for a in audits)
    assert any(a.action == "canary.post_verification_succeeded" for a in audits)
    dumped = json.dumps(result, default=str)
    assert "meta-token-secret" not in dumped


@pytest.mark.asyncio
async def test_post_verify_mismatch_and_no_auto_retry(monkeypatch):
    org_id, client_id, user_id, camp_id, _ = await _seed()
    _enable_canary(monkeypatch, org_id)
    mock_id = uuid.uuid4()
    mock_out = type(
        "Out",
        (),
        {
            "id": mock_id,
            "status": AIActionStatus.completed,
            "model_dump": lambda self, mode="json": {"id": str(self.id), "status": "completed"},
        },
    )()

    async def fake_get(*args, **kwargs):
        action = AIAction(
            organization_id=org_id,
            client_id=client_id,
            action_type=AIActionType.pause_campaign,
            agent=CANARY_AGENT,
            platform="meta",
            target_id=str(camp_id),
            description="canary",
            reason="canary",
            evidence=[],
            risk_level=RiskLevel.high,
            status=AIActionStatus.failed,
            result={"reconciliation": {"state": ReconciliationState.unknown.value}},
        )
        action.id = mock_id
        return action

    post = ReconciliationResult(
        outcome=ReconciliationOutcome.unknown,
        message="ambiguous",
        provider="meta",
        operation="pause_campaign",
    )
    reconcile_mock = AsyncMock(return_value=post)

    with (
        patch("app.automation.canary.ActionService.create", new=AsyncMock(return_value=mock_out)),
        patch("app.automation.canary.ActionService.approve", new=AsyncMock(return_value=mock_out)),
        patch("app.automation.canary.ActionService.get", new=AsyncMock(side_effect=fake_get)),
        patch("app.automation.canary.AdsReconciler.reconcile", new=reconcile_mock),
    ):
        async with AsyncSessionLocal() as db:
            result = await canary_execute(
                db,
                organization_id=org_id,
                provider="meta",
                action_type="pause_campaign",
                campaign_id=camp_id,
                external_campaign_id=None,
                client_id=client_id,
                actor_user_id=user_id,
                confirm=CANARY_CONFIRM_PHRASE,
            )
            await db.commit()
    assert result["post_verification"]["outcome"] == "UNKNOWN"
    assert reconcile_mock.await_count == 1  # no automatic retry


@pytest.mark.asyncio
async def test_google_canary_path_dry_run(monkeypatch):
    org_id, client_id, user_id, camp_id, _ = await _seed(
        connect_meta=False,
        connect_google=True,
        platform="google_ads",
        external_id="camp-canary-1",
    )
    _enable_canary(monkeypatch, org_id, provider="google_ads", action="pause_campaign")
    async with AsyncSessionLocal() as db:
        result = await canary_dry_run(
            db,
            organization_id=org_id,
            provider="google_ads",
            action_type="pause_campaign",
            campaign_id=camp_id,
            external_campaign_id=None,
            client_id=client_id,
            actor_user_id=user_id,
        )
    assert result["mutation"] is False
    assert result["gate"]["provider"] == "google_ads"


# ---- API / RBAC / tenant -----------------------------------------------------


@pytest.mark.asyncio
async def test_canary_api_rbac_and_tenant(monkeypatch):
    org_id, client_id, _, camp_id, email = await _seed()
    _enable_canary(monkeypatch, org_id)
    token = await _login(email)

    # member cannot dry-run / execute
    member_email = f"m5p2-m-{uuid.uuid4().hex[:8]}@test.com"
    async with AsyncSessionLocal() as db:
        org = await db.get(Organization, org_id)
        member = User(email=member_email, hashed_password=hash_password("pass"), full_name="Member")
        db.add(member)
        await db.flush()
        db.add(OrganizationMember(organization_id=org.id, user_id=member.id, role=MemberRole.member))
        await db.commit()
    mtoken = await _login(member_email)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        status = await client.get(
            "/api/v1/autopilot/operator/canary/status",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert status.status_code == 200
        body = status.json()
        assert body["canary_enabled"] is True
        assert "confirm_phrase" in body
        assert "access_token" not in json.dumps(body)

        dry = await client.post(
            "/api/v1/autopilot/operator/canary/dry-run",
            headers={"Authorization": f"Bearer {mtoken}"},
            json={"provider": "meta", "action_type": "pause_campaign", "campaign_id": str(camp_id)},
        )
        assert dry.status_code == 403

        exe = await client.post(
            "/api/v1/autopilot/operator/canary/execute",
            headers={"Authorization": f"Bearer {mtoken}"},
            json={
                "provider": "meta",
                "action_type": "pause_campaign",
                "campaign_id": str(camp_id),
                "confirm": CANARY_CONFIRM_PHRASE,
            },
        )
        assert exe.status_code == 403

        dry_ok = await client.post(
            "/api/v1/autopilot/operator/canary/dry-run",
            headers={"Authorization": f"Bearer {token}"},
            json={"provider": "meta", "action_type": "pause_campaign", "campaign_id": str(camp_id)},
        )
        assert dry_ok.status_code == 200
        assert dry_ok.json()["mutation"] is False

    # Tenant isolation: other org cannot see first org canary readiness as READY for their org
    org2, _, _, _, email2 = await _seed(connect_meta=False)
    token2 = await _login(email2)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        s2 = await client.get(
            "/api/v1/autopilot/operator/canary/status",
            headers={"Authorization": f"Bearer {token2}"},
        )
        assert s2.status_code == 200
        # org2 not allowlisted
        assert s2.json()["readiness"] in {"BLOCKED", "DISABLED", "NOT_CONFIGURED", "READY"}
        if s2.json()["canary_enabled"]:
            assert s2.json()["readiness"] != "READY" or org2 == org_id


@pytest.mark.asyncio
async def test_safe_demo_mode_defaults(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "canary_enabled", False)
    monkeypatch.setattr(settings, "autonomous_execution_enabled", False)
    monkeypatch.setattr(settings, "optimization_enabled", False)
    assert settings.canary_enabled is False
    org_id, client_id, _, camp_id, email = await _seed()
    token = await _login(email)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        s = await client.get(
            "/api/v1/autopilot/operator/canary/status",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert s.status_code == 200
        assert s.json()["readiness"] == "DISABLED"
        health = await client.get("/health/ready")
        assert health.status_code == 200
        assert health.json()["operational"]["live_canary"] == "DISABLED"


@pytest.mark.asyncio
async def test_ready_gate_path(monkeypatch):
    org_id, client_id, _, camp_id, _ = await _seed()
    _enable_canary(monkeypatch, org_id)
    async with AsyncSessionLocal() as db:
        gate = await evaluate_canary_gate(
            db,
            organization_id=org_id,
            provider="meta",
            action_type="pause_campaign",
            campaign_id=camp_id,
            client_id=client_id,
        )
    assert gate.allowed is True
    assert gate.readiness == "READY"
    assert gate.risk == RiskLevel.high  # pause is HIGH business risk even with 0 spend
    assert gate.spend_impact == 0.0
