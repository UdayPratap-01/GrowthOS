"""Milestone 6 — Real Meta verification (mocked Graph API; CI never needs live credentials)."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from httpx import ASGITransport, AsyncClient

from app.automation.canary import CANARY_CONFIRM_PHRASE, canary_execute, evaluate_canary_gate
from app.core.config import get_settings
from app.core.security import hash_password
from app.db.session import AsyncSessionLocal
from app.integrations.meta_family import MetaFamilyIntegration
from app.integrations.meta_oauth import (
    build_meta_connection_config,
    discover_meta_ad_accounts,
    exchange_for_long_lived_token,
)
from app.integrations.oauth import encode_oauth_state
from app.integrations.persistence import load_tokens, upsert_integration
from app.main import app
from app.models.ai_ops import AuditLog, Integration
from app.models.automation import AIAction, AutonomySettings
from app.models.client import Client
from app.models.enums import AIActionStatus, AIActionType, AutonomyMode, DataSource, MemberRole, RiskLevel
from app.models.marketing import Campaign
from app.models.organization import Organization, OrganizationMember
from app.models.user import User
from app.publishing.ads_executor import AdsExecutor
from app.publishing.ads_reconciliation import AdsReconciler, ReconciliationOutcome
from app.publishing.provider_errors import PROVIDER_TIMEOUT_AMBIGUOUS, classify_meta_graph_error
from app.schemas.autopilot import AIActionCreate, ActionDecision
from app.security.secrets import get_secret_store
from app.services.action_service import ActionService


class FakeResp:
    def __init__(self, status_code: int, data: dict | None = None, text: str = ""):
        self.status_code = status_code
        self._data = data or {}
        self.content = b"1" if data is not None or text else b""
        self.text = text or json.dumps(self._data)

    def json(self) -> dict:
        return self._data


# ---- Unit: error classification / discovery helpers --------------------------


def test_classify_meta_graph_errors():
    code, cat = classify_meta_graph_error(status_code=401, body={"error": {"code": 190, "message": "expired"}})
    assert code == "CREDENTIALS_EXPIRED"
    assert cat == "AUTHENTICATION"

    code, cat = classify_meta_graph_error(status_code=403, body={"error": {"code": 200, "message": "permission"}})
    assert code == "AUTHORIZATION_ERROR"

    code, cat = classify_meta_graph_error(status_code=429, body={})
    assert code == "RATE_LIMITED"

    code, cat = classify_meta_graph_error(status_code=404, body={"error": {"message": "does not exist"}})
    assert code == "TARGET_NOT_FOUND"

    code, cat = classify_meta_graph_error(status_code=400, body={"error": {"code": 100, "message": "Invalid parameter"}})
    assert code == "VALIDATION_ERROR"


def test_build_meta_connection_config_prefers_ad_account():
    cfg = build_meta_connection_config(
        me={"id": "user-1", "name": "User"},
        ad_accounts=[{"id": "act_111", "name": "Ads", "account_id": "111", "status": 1, "currency": "USD"}],
        display_name="Meta",
    )
    assert cfg["meta_user_id"] == "user-1"
    assert cfg["external_account_id"] == "act_111"
    assert cfg["ad_accounts"][0]["id"] == "act_111"
    dumped = json.dumps(cfg)
    assert "access_token" not in dumped


@pytest.mark.asyncio
async def test_long_lived_exchange_and_discovery(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "meta_app_id", "app")
    monkeypatch.setattr(settings, "meta_app_secret", "secret")

    class Client:
        async def get(self, url, **kwargs):
            if "fb_exchange_token" in str(kwargs.get("params") or {}):
                return FakeResp(200, {"access_token": "long-token", "expires_in": 5184000, "token_type": "bearer"})
            if "adaccounts" in url:
                return FakeResp(
                    200,
                    {"data": [{"id": "act_111", "account_id": "111", "name": "A", "account_status": 1}]},
                )
            return FakeResp(500, {"error": {"message": "nope"}})

        async def aclose(self):
            return None

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

    with patch("app.integrations.meta_oauth.httpx.AsyncClient", return_value=Client()):
        exchanged = await exchange_for_long_lived_token("short-token")
        assert exchanged["access_token"] == "long-token"
        accounts = await discover_meta_ad_accounts("long-token", http_client=Client())
    assert accounts[0]["id"] == "act_111"


# ---- Fixtures ---------------------------------------------------------------


async def _seed_meta_org(*, automation: bool = True):
    email = f"m6-{uuid.uuid4().hex[:8]}@test.com"
    async with AsyncSessionLocal() as db:
        org = Organization(name=f"M6 {uuid.uuid4().hex[:6]}", slug=f"m6-{uuid.uuid4().hex[:8]}", demo_mode=False)
        user = User(email=email, hashed_password=hash_password("pass"), full_name="M6")
        db.add_all([org, user])
        await db.flush()
        db.add(OrganizationMember(organization_id=org.id, user_id=user.id, role=MemberRole.owner))
        client = Client(organization_id=org.id, business_name="M6 Client", industry="saas")
        db.add(client)
        await db.flush()
        camp = Campaign(
            organization_id=org.id,
            client_id=client.id,
            name="M6 Camp",
            platform="meta",
            status="active",
            external_id="camp-m6-1",
            daily_budget=Decimal("10.00"),
            data_source=DataSource.live,
            metrics={"ad_account_id": "act_111"},
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
                maximum_campaign_budget=Decimal("100"),
                allowed_platforms=["meta"],
                allowed_actions=[a.value for a in AIActionType],
            )
        )
        ref = get_secret_store().store(
            json.dumps(
                {
                    "access_token": "meta-token-secret",
                    "expires_in": 5184000,
                    "obtained_at": datetime.now(timezone.utc).isoformat(),
                    "long_lived": True,
                    "provider": "meta",
                }
            )
        )
        db.add(
            Integration(
                organization_id=org.id,
                client_id=client.id,
                provider="meta",
                status="connected",
                secret_ref=ref,
                config={
                    "account_label": "Act",
                    "external_account_id": "act_111",
                    "meta_user_id": "user-1",
                    "ad_accounts": [{"id": "act_111", "name": "Act"}],
                    "last_verification": {
                        "status": "VERIFIED",
                        "checked_at": datetime.now(timezone.utc).isoformat(),
                        "safe_for_mutation": False,
                    },
                },
            )
        )
        await db.commit()
        return org.id, client.id, user.id, camp.id, email


def _enable_canary(monkeypatch, org_id):
    settings = get_settings()
    monkeypatch.setattr(settings, "canary_enabled", True)
    monkeypatch.setattr(settings, "canary_allowed_org_ids", str(org_id))
    monkeypatch.setattr(settings, "canary_allowed_providers", "meta")
    monkeypatch.setattr(settings, "canary_allowed_actions", "pause_campaign,resume_campaign,update_budget")
    monkeypatch.setattr(settings, "canary_allowed_environments", "test")
    monkeypatch.setattr(settings, "environment", "test")
    monkeypatch.setattr(settings, "canary_allowed_meta_ad_accounts", "act_111")
    monkeypatch.setattr(settings, "canary_allowed_meta_campaigns", "camp-m6-1")
    monkeypatch.setattr(settings, "canary_max_actions_per_day", 10)
    monkeypatch.setattr(settings, "canary_max_spend_impact", 50.0)
    monkeypatch.setattr(settings, "provider_verification_max_age_hours", 24)
    monkeypatch.setattr(settings, "autonomous_kill_switch", False)
    monkeypatch.setattr(settings, "meta_app_id", "app")
    monkeypatch.setattr(settings, "meta_app_secret", "secret")
    return settings


# ---- Executor: pause / resume / budget / timeout / auth ---------------------


@pytest.mark.asyncio
async def test_meta_pause_resume_budget_and_timeout(monkeypatch):
    org_id, client_id, user_id, camp_id, _ = await _seed_meta_org()
    monkeypatch.setattr(get_settings(), "demo_mode", False)
    monkeypatch.setattr(get_settings(), "meta_app_id", "app")
    monkeypatch.setattr(get_settings(), "meta_app_secret", "secret")

    calls: list[str] = []

    class Client:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return None

        async def post(self, url, data=None):
            calls.append(str(data.get("status") or data.get("daily_budget")))
            if data.get("status") == "TIMEOUT":
                raise httpx.TimeoutException("timeout")
            return FakeResp(200, {"success": True})

    async with AsyncSessionLocal() as db:
        camp = await db.get(Campaign, camp_id)
        action = AIAction(
            organization_id=org_id,
            client_id=client_id,
            action_type=AIActionType.pause_campaign,
            agent="test",
            platform="meta",
            target_id=str(camp_id),
            description="pause",
            reason="m6",
            evidence=[],
            risk_level=RiskLevel.high,
            status=AIActionStatus.approved,
            payload={},
        )
        db.add(action)
        await db.flush()

        with patch("app.publishing.ads_executor.httpx.AsyncClient", Client):
            with patch("app.publishing.ads_executor.ensure_meta_access_token", AsyncMock(return_value="tok")):
                pause = await AdsExecutor(db).execute(action, campaign=camp)
                assert pause.success is True
                assert camp.status == "paused"

                action.action_type = AIActionType.resume_campaign
                resume = await AdsExecutor(db).execute(action, campaign=camp)
                assert resume.success is True
                assert camp.status == "active"

                action.action_type = AIActionType.update_budget
                action.payload = {"daily_budget": "12.00"}
                budget = await AdsExecutor(db).execute(action, campaign=camp)
                assert budget.success is True
                assert str(camp.daily_budget) == "12.00"

        # Timeout → ambiguous, no blind retry success
        class TimeoutClient(Client):
            async def post(self, url, data=None):
                raise httpx.TimeoutException("timeout")

        with patch("app.publishing.ads_executor.httpx.AsyncClient", TimeoutClient):
            with patch("app.publishing.ads_executor.ensure_meta_access_token", AsyncMock(return_value="tok")):
                action.action_type = AIActionType.pause_campaign
                timed = await AdsExecutor(db).execute(action, campaign=camp)
        assert timed.ambiguous is True
        assert timed.error_code == PROVIDER_TIMEOUT_AMBIGUOUS
        await db.commit()


@pytest.mark.asyncio
async def test_meta_auth_and_rate_limit_errors(monkeypatch):
    org_id, client_id, _, camp_id, _ = await _seed_meta_org()
    monkeypatch.setattr(get_settings(), "demo_mode", False)

    class Client:
        def __init__(self, *a, **k):
            self.status = 401

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return None

        async def post(self, url, data=None):
            return FakeResp(
                self.status,
                {"error": {"code": 190 if self.status == 401 else 4, "message": "fail"}},
            )

    async with AsyncSessionLocal() as db:
        camp = await db.get(Campaign, camp_id)
        action = AIAction(
            organization_id=org_id,
            client_id=client_id,
            action_type=AIActionType.pause_campaign,
            agent="test",
            platform="meta",
            target_id=str(camp_id),
            description="pause",
            reason="m6",
            evidence=[],
            risk_level=RiskLevel.high,
            status=AIActionStatus.approved,
        )
        db.add(action)
        await db.flush()
        with patch("app.publishing.ads_executor.httpx.AsyncClient", Client):
            with patch("app.publishing.ads_executor.ensure_meta_access_token", AsyncMock(return_value="tok")):
                res = await AdsExecutor(db).execute(action, campaign=camp)
        assert res.success is False
        assert res.error_code == "CREDENTIALS_EXPIRED"

        Client.status = 429  # type: ignore[misc]
        c = Client
        c.status = 429

        class RateClient(Client):
            async def post(self, url, data=None):
                return FakeResp(429, {"error": {"code": 17, "message": "rate"}})

        with patch("app.publishing.ads_executor.httpx.AsyncClient", RateClient):
            with patch("app.publishing.ads_executor.ensure_meta_access_token", AsyncMock(return_value="tok")):
                res2 = await AdsExecutor(db).execute(action, campaign=camp)
        assert res2.error_code == "RATE_LIMITED"


@pytest.mark.asyncio
async def test_meta_reconciliation_pause_and_budget(monkeypatch):
    org_id, client_id, _, camp_id, _ = await _seed_meta_org()

    class Client:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return None

        async def get(self, url, **kwargs):
            return FakeResp(200, {"status": "PAUSED", "effective_status": "PAUSED", "daily_budget": 1200})

    async with AsyncSessionLocal() as db:
        camp = await db.get(Campaign, camp_id)
        action = AIAction(
            organization_id=org_id,
            client_id=client_id,
            action_type=AIActionType.pause_campaign,
            agent="test",
            platform="meta",
            target_id=str(camp_id),
            description="pause",
            reason="m6",
            evidence=[],
            risk_level=RiskLevel.high,
            status=AIActionStatus.completed,
            external_id="camp-m6-1",
        )
        db.add(action)
        await db.flush()
        with patch("app.publishing.ads_reconciliation.httpx.AsyncClient", Client):
            with patch(
                "app.integrations.meta_oauth.ensure_meta_access_token",
                AsyncMock(return_value="tok"),
            ):
                result = await AdsReconciler(db).reconcile(action, campaign=camp)
        assert result.outcome == ReconciliationOutcome.confirmed_success

        action.action_type = AIActionType.update_budget
        action.payload = {"daily_budget": "12.00"}

        class BudgetClient(Client):
            async def get(self, url, **kwargs):
                return FakeResp(200, {"status": "ACTIVE", "daily_budget": 1200})

        with patch("app.publishing.ads_reconciliation.httpx.AsyncClient", BudgetClient):
            with patch(
                "app.integrations.meta_oauth.ensure_meta_access_token",
                AsyncMock(return_value="tok"),
            ):
                budget_res = await AdsReconciler(db).reconcile(action, campaign=camp)
        assert budget_res.outcome in {
            ReconciliationOutcome.confirmed_success,
            ReconciliationOutcome.confirmed_not_applied,
            ReconciliationOutcome.unknown,
        }


@pytest.mark.asyncio
async def test_kill_switch_blocks_canary_without_meta_call(monkeypatch):
    org_id, client_id, user_id, camp_id, _ = await _seed_meta_org()
    settings = _enable_canary(monkeypatch, org_id)
    monkeypatch.setattr(settings, "autonomous_kill_switch", True)
    meta_called = {"n": 0}

    async def boom(*a, **k):
        meta_called["n"] += 1
        raise AssertionError("Meta must not be called")

    with patch("app.automation.canary.ActionService.create", new=AsyncMock(side_effect=boom)):
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
    assert result["executed"] is False
    assert result["blocked_code"] == "BLOCKED_KILL_SWITCH"
    assert meta_called["n"] == 0


@pytest.mark.asyncio
async def test_idempotent_duplicate_pause(monkeypatch):
    org_id, client_id, user_id, camp_id, _ = await _seed_meta_org()
    monkeypatch.setattr(get_settings(), "demo_mode", False)

    async with AsyncSessionLocal() as db:
        created = await ActionService(db).create(
            org_id,
            AIActionCreate(
                action_type=AIActionType.pause_campaign,
                client_id=client_id,
                agent="m6",
                platform="meta",
                target_id=str(camp_id),
                description="pause",
                reason="m6",
                estimated_cost=Decimal("0"),
                risk_level=RiskLevel.high,
                payload={"idempotency_key": f"m6-pause-{camp_id}"},
            ),
            user_id=user_id,
        )
        await db.commit()
        first_id = created.id

        created2 = await ActionService(db).create(
            org_id,
            AIActionCreate(
                action_type=AIActionType.pause_campaign,
                client_id=client_id,
                agent="m6",
                platform="meta",
                target_id=str(camp_id),
                description="pause",
                reason="m6",
                estimated_cost=Decimal("0"),
                risk_level=RiskLevel.high,
                payload={"idempotency_key": f"m6-pause-{camp_id}"},
            ),
            user_id=user_id,
        )
        await db.commit()
    assert created2.id == first_id


@pytest.mark.asyncio
async def test_oauth_callback_persists_ad_account(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "meta_app_id", "app")
    monkeypatch.setattr(settings, "meta_app_secret", "secret")
    monkeypatch.setattr(settings, "api_public_url", "http://test")

    email = f"m6o-{uuid.uuid4().hex[:8]}@test.com"
    async with AsyncSessionLocal() as db:
        org = Organization(name="OAuth Org", slug=f"oa-{uuid.uuid4().hex[:8]}", demo_mode=False)
        user = User(email=email, hashed_password=hash_password("pass"), full_name="O")
        db.add_all([org, user])
        await db.flush()
        db.add(OrganizationMember(organization_id=org.id, user_id=user.id, role=MemberRole.owner))
        biz = Client(organization_id=org.id, business_name="C", industry="saas")
        db.add(biz)
        await db.commit()
        org_id, client_id, user_id = org.id, biz.id, user.id

    state = encode_oauth_state(
        provider="meta", organization_id=org_id, client_id=client_id, user_id=user_id
    )

    class FakeHttp:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return None

        async def get(self, url, params=None):
            params = params or {}
            if "oauth/access_token" in url and "fb_exchange_token" not in params:
                return FakeResp(200, {"access_token": "short", "expires_in": 3600})
            if "fb_exchange_token" in (params or {}):
                return FakeResp(200, {"access_token": "long-lived-token", "expires_in": 5184000})
            if url.endswith("/me") or url.rstrip("/").endswith("/me"):
                return FakeResp(200, {"id": "user-99", "name": "Meta User"})
            if "adaccounts" in url:
                return FakeResp(
                    200,
                    {"data": [{"id": "act_222", "account_id": "222", "name": "Ads 222", "account_status": 1}]},
                )
            return FakeResp(404, {})

    with patch("app.integrations.meta_family.httpx.AsyncClient", return_value=FakeHttp()):
        with patch("app.integrations.meta_oauth.httpx.AsyncClient", return_value=FakeHttp()):
            async with AsyncSessionLocal() as db:
                integ = MetaFamilyIntegration("meta", "Meta Ads")
                integ._db = db  # type: ignore[attr-defined]
                result = await integ.handle_callback(code="auth-code", state=state)
                await db.commit()
                from sqlalchemy import select

                row = await db.scalar(
                    select(Integration).where(
                        Integration.organization_id == org_id, Integration.provider == "meta"
                    )
                )

    assert result["ad_account_count"] == 1
    assert result["long_lived_token"] is True
    assert row is not None
    assert row.config["external_account_id"] == "act_222"
    assert row.config["meta_user_id"] == "user-99"
    tokens = load_tokens(row)
    assert tokens["access_token"] == "long-lived-token"
    assert tokens.get("long_lived") is True
    assert "access_token" not in json.dumps(result)


@pytest.mark.asyncio
async def test_tenant_isolation_meta_action(monkeypatch):
    org_a, client_a, user_a, camp_a, email_a = await _seed_meta_org()
    org_b, _, _, _, email_b = await _seed_meta_org()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as http:
        login_b = await http.post("/api/v1/auth/login", json={"email": email_b, "password": "pass"})
        token_b = login_b.json()["access_token"]
        res = await http.post(
            "/api/v1/autopilot/operator/canary/dry-run",
            headers={"Authorization": f"Bearer {token_b}"},
            json={"provider": "meta", "action_type": "pause_campaign", "campaign_id": str(camp_a)},
        )
        assert res.status_code in {200, 403, 404}
        if res.status_code == 200:
            body = res.json()
            assert body.get("eligible") is False or body.get("mutation") is False


@pytest.mark.asyncio
async def test_no_secrets_in_executor_platform_response(monkeypatch):
    org_id, client_id, _, camp_id, _ = await _seed_meta_org()
    monkeypatch.setattr(get_settings(), "demo_mode", False)

    class FakeHttp:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return None

        async def post(self, url, data=None):
            return FakeResp(400, {"error": {"message": "bad", "access_token": "should-strip"}, "access_token": "x"})

    async with AsyncSessionLocal() as db:
        camp = await db.get(Campaign, camp_id)
        action = AIAction(
            organization_id=org_id,
            client_id=client_id,
            action_type=AIActionType.pause_campaign,
            agent="test",
            platform="meta",
            target_id=str(camp_id),
            description="pause",
            reason="m6",
            evidence=[],
            risk_level=RiskLevel.high,
            status=AIActionStatus.approved,
        )
        db.add(action)
        await db.flush()
        with patch("app.publishing.ads_executor.httpx.AsyncClient", FakeHttp):
            with patch("app.publishing.ads_executor.ensure_meta_access_token", AsyncMock(return_value="tok")):
                res = await AdsExecutor(db).execute(action, campaign=camp)
        assert "access_token" not in (res.platform_response.get("body") or res.platform_response)
