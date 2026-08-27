"""Milestone 5 Phase 1 — provider preflight + read-only verification."""

from __future__ import annotations

import json
import uuid
from decimal import Decimal
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.core.config import get_settings
from app.core.security import hash_password
from app.db.session import AsyncSessionLocal
from app.main import app
from app.models.ai_ops import AuditLog, Integration
from app.models.automation import AIAction, AutonomySettings
from app.models.client import Client
from app.models.enums import AIActionType, AutonomyMode, MemberRole
from app.models.organization import Organization, OrganizationMember
from app.models.user import User
from app.publishing.provider_preflight import PreflightStatus, run_provider_preflight
from app.publishing.provider_verification import (
    READ_ONLY_CONFIRM_PHRASE,
    VerificationReport,
    require_read_only_confirm,
    verify_meta_campaign_ops,
    verify_provider_readonly,
)
from app.security.secrets import get_secret_store


class FakeResp:
    def __init__(self, status_code: int, data: dict | None = None, text: str = ""):
        self.status_code = status_code
        self._data = data or {}
        self.text = text or json.dumps(self._data)

    def json(self) -> dict:
        return self._data


class FakeHttp:
    def __init__(self, mapping: dict[str, FakeResp]):
        self.mapping = mapping
        self.calls: list[tuple[str, str]] = []

    def _match(self, method: str, url: str) -> FakeResp:
        self.calls.append((method, url))
        best: FakeResp | None = None
        best_len = -1
        for key, resp in self.mapping.items():
            if key in url and len(key) > best_len:
                best = resp
                best_len = len(key)
        return best or FakeResp(500, {"error": "unmatched"}, text="unmatched")

    async def get(self, url: str, **kwargs: Any) -> FakeResp:
        return self._match("GET", url)

    async def post(self, url: str, **kwargs: Any) -> FakeResp:
        return self._match("POST", url)

    async def aclose(self) -> None:
        return None


async def _seed_org(*, connect_meta: bool = False, connect_google: bool = False, partial_meta: bool = False):
    email = f"m5-{uuid.uuid4().hex[:8]}@test.com"
    async with AsyncSessionLocal() as db:
        org = Organization(name=f"M5 {uuid.uuid4().hex[:6]}", slug=f"m5-{uuid.uuid4().hex[:8]}", demo_mode=False)
        user = User(email=email, hashed_password=hash_password("pass"), full_name="M5")
        db.add_all([org, user])
        await db.flush()
        db.add(OrganizationMember(organization_id=org.id, user_id=user.id, role=MemberRole.owner))
        client = Client(organization_id=org.id, business_name="M5 Client", industry="saas")
        db.add(client)
        await db.flush()
        db.add(
            AutonomySettings(
                organization_id=org.id,
                autonomy_mode=AutonomyMode.copilot,
                automation_enabled=False,
                allowed_actions=[a.value for a in AIActionType],
            )
        )
        if connect_meta:
            ref = get_secret_store().store(json.dumps({"access_token": "meta-token-secret"}))
            db.add(
                Integration(
                    organization_id=org.id,
                    client_id=client.id,
                    provider="meta",
                    status="connected",
                    secret_ref=ref,
                    config={"account_label": "Act 1", "external_account_id": "act_111"},
                )
            )
        if connect_google:
            ref = get_secret_store().store(
                json.dumps({"access_token": "google-token-secret", "refresh_token": "refresh-secret"})
            )
            db.add(
                Integration(
                    organization_id=org.id,
                    client_id=client.id,
                    provider="google_ads",
                    status="connected",
                    secret_ref=ref,
                    config={"account_label": "G Ads", "external_account_id": "999"},
                )
            )
        await db.commit()
        return org.id, client.id, user.id, email


@pytest.mark.asyncio
async def test_preflight_not_configured(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "meta_app_id", "")
    monkeypatch.setattr(settings, "meta_app_secret", "")
    monkeypatch.setattr(settings, "google_client_id", "")
    monkeypatch.setattr(settings, "google_client_secret", "")
    monkeypatch.setattr(settings, "google_ads_developer_token", "")
    org_id, client_id, _, _ = await _seed_org()
    async with AsyncSessionLocal() as db:
        meta = await run_provider_preflight(db, organization_id=org_id, provider="meta", client_id=client_id)
        google = await run_provider_preflight(
            db, organization_id=org_id, provider="google_ads", client_id=client_id
        )
    assert meta.status in {PreflightStatus.not_configured, PreflightStatus.demo}
    assert google.status in {PreflightStatus.not_configured, PreflightStatus.demo}
    assert meta.safe_for_mutation is False


@pytest.mark.asyncio
async def test_preflight_partial_and_connected(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "meta_app_id", "app")
    monkeypatch.setattr(settings, "meta_app_secret", "")
    monkeypatch.setattr(settings, "google_client_id", "cid")
    monkeypatch.setattr(settings, "google_client_secret", "csec")
    monkeypatch.setattr(settings, "google_ads_developer_token", "")
    org_id, client_id, _, _ = await _seed_org(connect_meta=True)
    async with AsyncSessionLocal() as db:
        meta = await run_provider_preflight(db, organization_id=org_id, provider="meta", client_id=client_id)
        google = await run_provider_preflight(
            db, organization_id=org_id, provider="google_ads", client_id=client_id
        )
    assert meta.status == PreflightStatus.partially_configured
    assert google.status == PreflightStatus.partially_configured


@pytest.mark.asyncio
async def test_verify_requires_confirm(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "meta_app_id", "app")
    monkeypatch.setattr(settings, "meta_app_secret", "secret")
    org_id, client_id, user_id, _ = await _seed_org(connect_meta=True)
    async with AsyncSessionLocal() as db:
        report = await verify_provider_readonly(
            db,
            organization_id=org_id,
            provider="meta",
            client_id=client_id,
            confirm="nope",
            actor_user_id=user_id,
        )
        await db.commit()
        actions = list((await db.scalars(select(AIAction).where(AIAction.organization_id == org_id))).all())
    assert report.status == "BLOCKED"
    assert report.ran is False
    assert report.safe_for_mutation is False
    assert actions == []


@pytest.mark.asyncio
async def test_meta_readonly_verification_success(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "meta_app_id", "app")
    monkeypatch.setattr(settings, "meta_app_secret", "secret")
    org_id, client_id, user_id, _ = await _seed_org(connect_meta=True)
    http = FakeHttp(
        {
            "/me/adaccounts": FakeResp(
                200,
                {
                    "data": [
                        {
                            "id": "act_111",
                            "account_id": "111",
                            "name": "Test Act",
                            "currency": "USD",
                            "timezone_name": "UTC",
                            "account_status": 1,
                        }
                    ]
                },
            ),
            "/me": FakeResp(200, {"id": "u1", "name": "User"}),
            "/campaigns": FakeResp(200, {"data": [{"id": "c1", "name": "Camp"}]}),
        }
    )
    async with AsyncSessionLocal() as db:
        report = await verify_provider_readonly(
            db,
            organization_id=org_id,
            provider="meta",
            client_id=client_id,
            confirm=READ_ONLY_CONFIRM_PHRASE,
            actor_user_id=user_id,
            http_client=http,
        )
        await db.commit()
        actions = list((await db.scalars(select(AIAction).where(AIAction.organization_id == org_id))).all())
        audits = list(
            (
                await db.scalars(
                    select(AuditLog).where(
                        AuditLog.organization_id == org_id,
                        AuditLog.action.like("provider.%"),
                    )
                )
            ).all()
        )
        row = await db.scalar(
            select(Integration).where(Integration.organization_id == org_id, Integration.provider == "meta")
        )
    assert report.status == "VERIFIED"
    assert report.safe_for_read is True
    assert report.safe_for_mutation is False
    assert actions == []
    assert any(a.action == "provider.verification_succeeded" for a in audits)
    assert row is not None
    dumped = json.dumps(row.config or {})
    assert "meta-token-secret" not in dumped
    assert "access_token" not in dumped
    # Never called mutate endpoints
    assert not any("mutate" in u.lower() for _, u in http.calls)


@pytest.mark.asyncio
async def test_meta_auth_failure(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "meta_app_id", "app")
    monkeypatch.setattr(settings, "meta_app_secret", "secret")
    org_id, client_id, user_id, _ = await _seed_org(connect_meta=True)
    http = FakeHttp({"/me": FakeResp(401, {"error": {"message": "Invalid OAuth"}}, text="Invalid OAuth")})
    async with AsyncSessionLocal() as db:
        report = await verify_provider_readonly(
            db,
            organization_id=org_id,
            provider="meta",
            client_id=client_id,
            confirm=READ_ONLY_CONFIRM_PHRASE,
            actor_user_id=user_id,
            http_client=http,
        )
    assert report.status == "VERIFICATION_FAILED"
    assert report.authentication.get("status") == "AUTHENTICATION_FAILED"
    assert report.safe_for_mutation is False


@pytest.mark.asyncio
async def test_google_readonly_verification_success(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "google_client_id", "cid")
    monkeypatch.setattr(settings, "google_client_secret", "csec")
    monkeypatch.setattr(settings, "google_ads_developer_token", "devtok")
    org_id, client_id, user_id, _ = await _seed_org(connect_google=True)
    http = FakeHttp(
        {
            "listAccessibleCustomers": FakeResp(200, {"resourceNames": ["customers/999"]}),
            "googleAds:search": FakeResp(200, {"results": [{"campaign": {"id": "1"}}]}),
        }
    )
    async with AsyncSessionLocal() as db:
        report = await verify_provider_readonly(
            db,
            organization_id=org_id,
            provider="google_ads",
            client_id=client_id,
            confirm=READ_ONLY_CONFIRM_PHRASE,
            actor_user_id=user_id,
            http_client=http,
        )
        await db.commit()
        actions = list((await db.scalars(select(AIAction).where(AIAction.organization_id == org_id))).all())
    assert report.status == "VERIFIED"
    assert report.safe_for_read is True
    assert report.safe_for_mutation is False
    assert actions == []
    assert not any("mutate" in u.lower() for _, u in http.calls)


@pytest.mark.asyncio
async def test_google_account_inaccessible(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "google_client_id", "cid")
    monkeypatch.setattr(settings, "google_client_secret", "csec")
    monkeypatch.setattr(settings, "google_ads_developer_token", "devtok")
    org_id, client_id, user_id, _ = await _seed_org(connect_google=True)
    http = FakeHttp({"listAccessibleCustomers": FakeResp(200, {"resourceNames": []})})
    async with AsyncSessionLocal() as db:
        report = await verify_provider_readonly(
            db,
            organization_id=org_id,
            provider="google_ads",
            client_id=client_id,
            confirm=READ_ONLY_CONFIRM_PHRASE,
            actor_user_id=user_id,
            http_client=http,
        )
    assert report.status == "VERIFICATION_FAILED"
    assert report.error_category == "ACCOUNT_NOT_FOUND"


@pytest.mark.asyncio
async def test_tenant_isolation_verify(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "meta_app_id", "app")
    monkeypatch.setattr(settings, "meta_app_secret", "secret")
    org_a, client_a, user_a, _ = await _seed_org(connect_meta=True)
    org_b, _, user_b, _ = await _seed_org()
    http = FakeHttp({"/me": FakeResp(200, {"id": "u"})})
    async with AsyncSessionLocal() as db:
        # Org B cannot see Org A connection — preflight not connected
        report = await verify_provider_readonly(
            db,
            organization_id=org_b,
            provider="meta",
            client_id=None,
            confirm=READ_ONLY_CONFIRM_PHRASE,
            actor_user_id=user_b,
            http_client=http,
        )
    assert report.status in {"NOT_CONNECTED", "NOT_CONFIGURED", "VERIFICATION_FAILED", "PARTIALLY_CONFIGURED"}
    assert report.status != "VERIFIED"


@pytest.mark.asyncio
async def test_operator_provider_apis_rbac(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "meta_app_id", "")
    monkeypatch.setattr(settings, "meta_app_secret", "")
    org_id, _, _, email = await _seed_org()
    # member
    member_email = f"m5m-{uuid.uuid4().hex[:8]}@test.com"
    async with AsyncSessionLocal() as db:
        user = User(email=member_email, hashed_password=hash_password("pass"), full_name="Mem")
        db.add(user)
        await db.flush()
        db.add(OrganizationMember(organization_id=org_id, user_id=user.id, role=MemberRole.member))
        await db.commit()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        login = await client.post("/api/v1/auth/login", json={"email": email, "password": "pass"})
        token = login.json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}
        listed = await client.get("/api/v1/autopilot/operator/providers", headers=headers)
        assert listed.status_code == 200
        assert "items" in listed.json()
        pre = await client.post("/api/v1/autopilot/operator/providers/meta/preflight", headers=headers, json={})
        assert pre.status_code == 200
        assert pre.json()["safe_for_mutation"] is False

        mlogin = await client.post("/api/v1/auth/login", json={"email": member_email, "password": "pass"})
        mheaders = {"Authorization": f"Bearer {mlogin.json()['access_token']}"}
        denied = await client.post(
            "/api/v1/autopilot/operator/providers/meta/verify",
            headers=mheaders,
            json={"confirm": READ_ONLY_CONFIRM_PHRASE},
        )
        assert denied.status_code == 403


def test_confirm_phrase_and_cli_gate():
    ok, _ = require_read_only_confirm(READ_ONLY_CONFIRM_PHRASE)
    assert ok is True
    bad, reason = require_read_only_confirm("x")
    assert bad is False and reason


@pytest.mark.asyncio
async def test_cli_meta_ops_still_blocks_mutations():
    report = await verify_meta_campaign_ops(dry_run=True)
    assert report.safe_for_mutation is False
    assert isinstance(report, VerificationReport)


@pytest.mark.asyncio
async def test_verify_never_imports_action_service(monkeypatch):
    """Guard: verification module must not call ActionService during success path."""
    settings = get_settings()
    monkeypatch.setattr(settings, "meta_app_id", "app")
    monkeypatch.setattr(settings, "meta_app_secret", "secret")
    org_id, client_id, user_id, _ = await _seed_org(connect_meta=True)
    http = FakeHttp(
        {
            "/me": FakeResp(200, {"id": "u"}),
            "/me/adaccounts": FakeResp(
                200, {"data": [{"id": "act_111", "account_id": "111", "name": "A", "currency": "USD"}]}
            ),
            "/campaigns": FakeResp(200, {"data": []}),
        }
    )
    with patch("app.services.action_service.ActionService") as mocked:
        async with AsyncSessionLocal() as db:
            await verify_provider_readonly(
                db,
                organization_id=org_id,
                provider="meta",
                client_id=client_id,
                confirm=READ_ONLY_CONFIRM_PHRASE,
                actor_user_id=user_id,
                http_client=http,
            )
        mocked.assert_not_called()
