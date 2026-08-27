"""Milestone 7 — Real Google Ads verification (mocked API; CI needs no live credentials)."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.automation.canary import CANARY_CONFIRM_PHRASE, canary_execute
from app.core.config import get_settings
from app.core.security import hash_password
from app.db.session import AsyncSessionLocal
from app.integrations.google_ads import GoogleAdsIntegration
from app.integrations.google_ads_discovery import (
    build_google_connection_config,
    discover_google_customers,
    resolve_google_customer_id,
)
from app.integrations.oauth import encode_oauth_state
from app.integrations.persistence import load_tokens
from app.main import app
from app.models.ai_ops import Integration
from app.models.automation import AIAction, AutonomySettings
from app.models.client import Client
from app.models.enums import AIActionStatus, AIActionType, AutonomyMode, DataSource, MemberRole, RiskLevel
from app.models.marketing import Campaign
from app.models.organization import Organization, OrganizationMember
from app.models.user import User
from app.publishing.ads_executor import AdsExecutor
from app.publishing.ads_reconciliation import AdsReconciler, ReconciliationOutcome
from app.publishing.provider_errors import PROVIDER_TIMEOUT_AMBIGUOUS, classify_google_ads_error
from app.security.secrets import get_secret_store


class FakeResp:
    def __init__(self, status_code: int, data: dict | None = None, text: str = ""):
        self.status_code = status_code
        self._data = data or {}
        self.content = b"1" if data is not None or text else b""
        self.text = text or json.dumps(self._data)

    def json(self) -> dict:
        return self._data


def test_classify_google_ads_errors():
    code, cat = classify_google_ads_error(
        status_code=401, body={"error": {"status": "UNAUTHENTICATED", "message": "invalid_grant"}}
    )
    assert code == "CREDENTIALS_EXPIRED"
    assert cat == "AUTHENTICATION"

    code, cat = classify_google_ads_error(
        status_code=403, body={"error": {"message": "The developer token is not approved"}}
    )
    assert code == "AUTHORIZATION_ERROR"

    code, cat = classify_google_ads_error(status_code=429, body={"error": {"status": "RESOURCE_EXHAUSTED"}})
    assert code == "RATE_LIMITED"

    code, cat = classify_google_ads_error(status_code=404, body={"error": {"status": "NOT_FOUND"}})
    assert code == "TARGET_NOT_FOUND"


def test_build_google_connection_config():
    cfg = build_google_connection_config(
        customers=[
            {"id": "111", "resource_name": "customers/111", "name": "Google Ads / 111"},
            {"id": "222", "resource_name": "customers/222", "name": "Google Ads / 222"},
        ],
        preferred_customer_id="222",
    )
    assert cfg["customer_id"] == "222"
    assert cfg["external_account_id"] == "222"
    assert len(cfg["customers"]) == 2
    assert "access_token" not in json.dumps(cfg)


def test_resolve_google_customer_id_precedence():
    assert (
        resolve_google_customer_id(
            campaign_metrics={"customer_id": "111"},
            integration_config={"customer_id": "222"},
            login_customer_id="333",
        )
        == "111"
    )
    assert (
        resolve_google_customer_id(
            campaign_metrics={},
            integration_config={"customer_id": "222"},
            login_customer_id="333",
        )
        == "222"
    )


@pytest.mark.asyncio
async def test_discover_google_customers(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "google_ads_developer_token", "devtok")

    class FakeHttp:
        async def get(self, url, **kwargs):
            assert "listAccessibleCustomers" in url
            assert "developer-token" in (kwargs.get("headers") or {})
            return FakeResp(200, {"resourceNames": ["customers/999", "customers/888"]})

        async def aclose(self):
            return None

    customers = await discover_google_customers("tok", http_client=FakeHttp())
    assert [c["id"] for c in customers] == ["999", "888"]


async def _seed_google_org(*, automation: bool = True):
    email = f"m7-{uuid.uuid4().hex[:8]}@test.com"
    async with AsyncSessionLocal() as db:
        org = Organization(name=f"M7 {uuid.uuid4().hex[:6]}", slug=f"m7-{uuid.uuid4().hex[:8]}", demo_mode=False)
        user = User(email=email, hashed_password=hash_password("pass"), full_name="M7")
        db.add_all([org, user])
        await db.flush()
        db.add(OrganizationMember(organization_id=org.id, user_id=user.id, role=MemberRole.owner))
        client = Client(organization_id=org.id, business_name="M7 Client", industry="saas")
        db.add(client)
        await db.flush()
        camp = Campaign(
            organization_id=org.id,
            client_id=client.id,
            name="M7 Camp",
            platform="google_ads",
            status="active",
            external_id="camp-m7-1",
            daily_budget=Decimal("10.00"),
            data_source=DataSource.live,
            metrics={"customer_id": "999", "external_campaign_id": "camp-m7-1"},
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
                allowed_platforms=["google_ads", "google"],
                allowed_actions=[a.value for a in AIActionType],
            )
        )
        ref = get_secret_store().store(
            json.dumps(
                {
                    "access_token": "google-token-secret",
                    "refresh_token": "refresh-secret",
                    "expires_in": 3600,
                    "obtained_at": datetime.now(timezone.utc).isoformat(),
                    "provider": "google_ads",
                }
            )
        )
        db.add(
            Integration(
                organization_id=org.id,
                client_id=client.id,
                provider="google_ads",
                status="connected",
                secret_ref=ref,
                config={
                    "account_label": "G Ads",
                    "customer_id": "999",
                    "external_account_id": "999",
                    "customers": [{"id": "999", "name": "G Ads"}],
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
    monkeypatch.setattr(settings, "canary_allowed_providers", "google_ads")
    monkeypatch.setattr(settings, "canary_allowed_actions", "pause_campaign,resume_campaign")
    monkeypatch.setattr(settings, "canary_allowed_environments", "test")
    monkeypatch.setattr(settings, "environment", "test")
    monkeypatch.setattr(settings, "canary_allowed_google_customers", "999")
    monkeypatch.setattr(settings, "canary_allowed_google_campaigns", "camp-m7-1")
    monkeypatch.setattr(settings, "canary_max_actions_per_day", 10)
    monkeypatch.setattr(settings, "provider_verification_max_age_hours", 24)
    monkeypatch.setattr(settings, "autonomous_kill_switch", False)
    monkeypatch.setattr(settings, "google_client_id", "cid")
    monkeypatch.setattr(settings, "google_client_secret", "csec")
    monkeypatch.setattr(settings, "google_ads_developer_token", "devtok")
    return settings


@pytest.mark.asyncio
async def test_google_pause_resume_timeout_and_auth(monkeypatch):
    org_id, client_id, _, camp_id, _ = await _seed_google_org()
    monkeypatch.setattr(get_settings(), "demo_mode", False)
    monkeypatch.setattr(get_settings(), "google_ads_developer_token", "devtok")
    posted: list[str] = []

    class FakeHttp:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return None

        async def post(self, url, headers=None, json=None):  # noqa: A002 — matches httpx API
            posted.append(url)
            assert "campaigns:mutate" in url
            assert "developer-token" not in __import__("json").dumps(json or {})
            return FakeResp(200, {"results": [{"resourceName": "customers/999/campaigns/camp-m7-1"}]})

    async with AsyncSessionLocal() as db:
        camp = await db.get(Campaign, camp_id)
        action = AIAction(
            organization_id=org_id,
            client_id=client_id,
            action_type=AIActionType.pause_campaign,
            agent="test",
            platform="google_ads",
            target_id=str(camp_id),
            description="pause",
            reason="m7",
            evidence=[],
            risk_level=RiskLevel.high,
            status=AIActionStatus.approved,
        )
        db.add(action)
        await db.flush()
        with patch("app.publishing.ads_executor.httpx.AsyncClient", FakeHttp):
            with patch("app.publishing.ads_executor.ensure_access_token", AsyncMock(return_value="tok")):
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
                assert budget.success is False
                assert budget.error_code == "UNSUPPORTED_OPERATION"

        class TimeoutHttp(FakeHttp):
            async def post(self, url, headers=None, json=None):
                raise httpx.TimeoutException("timeout")

        with patch("app.publishing.ads_executor.httpx.AsyncClient", TimeoutHttp):
            with patch("app.publishing.ads_executor.ensure_access_token", AsyncMock(return_value="tok")):
                action.action_type = AIActionType.pause_campaign
                timed = await AdsExecutor(db).execute(action, campaign=camp)
        assert timed.ambiguous is True
        assert timed.error_code == PROVIDER_TIMEOUT_AMBIGUOUS

        class AuthHttp(FakeHttp):
            async def post(self, url, headers=None, json=None):
                return FakeResp(401, {"error": {"status": "UNAUTHENTICATED", "message": "expired"}})

        with patch("app.publishing.ads_executor.httpx.AsyncClient", AuthHttp):
            with patch("app.publishing.ads_executor.ensure_access_token", AsyncMock(return_value="tok")):
                auth = await AdsExecutor(db).execute(action, campaign=camp)
        assert auth.error_code == "CREDENTIALS_EXPIRED"
        assert all("campaigns:mutate" in u for u in posted)
        await db.commit()


@pytest.mark.asyncio
async def test_google_reconciliation_pause(monkeypatch):
    org_id, client_id, _, camp_id, _ = await _seed_google_org()
    monkeypatch.setattr(get_settings(), "google_ads_developer_token", "devtok")

    class FakeHttp:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return None

        async def post(self, url, headers=None, json=None):
            return FakeResp(200, {"results": [{"campaign": {"status": "PAUSED"}}]})

    async with AsyncSessionLocal() as db:
        camp = await db.get(Campaign, camp_id)
        action = AIAction(
            organization_id=org_id,
            client_id=client_id,
            action_type=AIActionType.pause_campaign,
            agent="test",
            platform="google_ads",
            target_id=str(camp_id),
            description="pause",
            reason="m7",
            evidence=[],
            risk_level=RiskLevel.high,
            status=AIActionStatus.completed,
            external_id="camp-m7-1",
        )
        db.add(action)
        await db.flush()
        with patch("app.publishing.ads_reconciliation.httpx.AsyncClient", FakeHttp):
            with patch("app.publishing.ads_reconciliation.ensure_access_token", AsyncMock(return_value="tok")):
                result = await AdsReconciler(db).reconcile(action, campaign=camp)
        assert result.outcome == ReconciliationOutcome.confirmed_success


@pytest.mark.asyncio
async def test_kill_switch_blocks_google_canary(monkeypatch):
    org_id, client_id, user_id, camp_id, _ = await _seed_google_org()
    settings = _enable_canary(monkeypatch, org_id)
    monkeypatch.setattr(settings, "autonomous_kill_switch", True)
    called = {"n": 0}

    async def boom(*a, **k):
        called["n"] += 1
        raise AssertionError("must not create action")

    with patch("app.automation.canary.ActionService.create", new=AsyncMock(side_effect=boom)):
        async with AsyncSessionLocal() as db:
            result = await canary_execute(
                db,
                organization_id=org_id,
                provider="google_ads",
                action_type="pause_campaign",
                campaign_id=camp_id,
                external_campaign_id=None,
                client_id=client_id,
                actor_user_id=user_id,
                confirm=CANARY_CONFIRM_PHRASE,
            )
    assert result["blocked_code"] == "BLOCKED_KILL_SWITCH"
    assert called["n"] == 0


@pytest.mark.asyncio
async def test_oauth_callback_persists_customers(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "google_client_id", "cid")
    monkeypatch.setattr(settings, "google_client_secret", "csec")
    monkeypatch.setattr(settings, "google_ads_developer_token", "devtok")
    monkeypatch.setattr(settings, "api_public_url", "http://test")

    email = f"m7o-{uuid.uuid4().hex[:8]}@test.com"
    async with AsyncSessionLocal() as db:
        org = Organization(name="GO Auth", slug=f"go-{uuid.uuid4().hex[:8]}", demo_mode=False)
        user = User(email=email, hashed_password=hash_password("pass"), full_name="G")
        db.add_all([org, user])
        await db.flush()
        db.add(OrganizationMember(organization_id=org.id, user_id=user.id, role=MemberRole.owner))
        biz = Client(organization_id=org.id, business_name="C", industry="saas")
        db.add(biz)
        await db.commit()
        org_id, client_id, user_id = org.id, biz.id, user.id

    state = encode_oauth_state(
        provider="google_ads", organization_id=org_id, client_id=client_id, user_id=user_id
    )

    with patch(
        "app.integrations.google_ads.exchange_code",
        AsyncMock(
            return_value={
                "access_token": "access-secret",
                "refresh_token": "refresh-secret",
                "expires_in": 3600,
                "token_type": "Bearer",
            }
        ),
    ):
        with patch(
            "app.integrations.google_ads_discovery.discover_google_customers",
            AsyncMock(
                return_value=[
                    {"id": "555", "resource_name": "customers/555", "name": "Google Ads / 555"},
                    {"id": "666", "resource_name": "customers/666", "name": "Google Ads / 666"},
                ]
            ),
        ):
            async with AsyncSessionLocal() as db:
                integ = GoogleAdsIntegration()
                integ._db = db  # type: ignore[attr-defined]
                result = await integ.handle_callback(code="auth-code", state=state)
                await db.commit()
                row = await db.scalar(
                    select(Integration).where(
                        Integration.organization_id == org_id, Integration.provider == "google_ads"
                    )
                )

    assert result["customer_count"] == 2
    assert row is not None
    assert row.config["customer_id"] == "555"
    assert len(row.config["customers"]) == 2
    tokens = load_tokens(row)
    assert tokens["refresh_token"] == "refresh-secret"
    assert "access_token" not in json.dumps(result)


@pytest.mark.asyncio
async def test_tenant_isolation_google_canary(monkeypatch):
    _, _, _, camp_a, _ = await _seed_google_org()
    _, _, _, _, email_b = await _seed_google_org()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as http:
        login_b = await http.post("/api/v1/auth/login", json={"email": email_b, "password": "pass"})
        token_b = login_b.json()["access_token"]
        res = await http.post(
            "/api/v1/autopilot/operator/canary/dry-run",
            headers={"Authorization": f"Bearer {token_b}"},
            json={"provider": "google_ads", "action_type": "pause_campaign", "campaign_id": str(camp_a)},
        )
        assert res.status_code in {200, 403, 404}
        if res.status_code == 200:
            assert res.json().get("eligible") is False or res.json().get("mutation") is False


@pytest.mark.asyncio
async def test_no_secrets_in_google_error_response(monkeypatch):
    org_id, client_id, _, camp_id, _ = await _seed_google_org()
    monkeypatch.setattr(get_settings(), "demo_mode", False)
    monkeypatch.setattr(get_settings(), "google_ads_developer_token", "devtok")

    class FakeHttp:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return None

        async def post(self, url, headers=None, json=None):
            return FakeResp(
                400,
                {"error": {"message": "bad"}, "access_token": "should-strip", "refresh_token": "nope"},
            )

    async with AsyncSessionLocal() as db:
        camp = await db.get(Campaign, camp_id)
        action = AIAction(
            organization_id=org_id,
            client_id=client_id,
            action_type=AIActionType.pause_campaign,
            agent="test",
            platform="google_ads",
            target_id=str(camp_id),
            description="pause",
            reason="m7",
            evidence=[],
            risk_level=RiskLevel.high,
            status=AIActionStatus.approved,
        )
        db.add(action)
        await db.flush()
        with patch("app.publishing.ads_executor.httpx.AsyncClient", FakeHttp):
            with patch("app.publishing.ads_executor.ensure_access_token", AsyncMock(return_value="tok")):
                res = await AdsExecutor(db).execute(action, campaign=camp)
        body = res.platform_response.get("body") or res.platform_response
        assert "access_token" not in body
        assert "refresh_token" not in body
