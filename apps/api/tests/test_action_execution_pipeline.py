"""AI action execution pipeline — idempotency, tenant checks, ads executor honesty."""

from __future__ import annotations

import uuid

import httpx
import pytest
from httpx import ASGITransport, AsyncClient

from app.automation.execution import ExecutionEngine
from app.automation.idempotency import (
    build_action_idempotency_key,
    sanitize_platform_response,
    try_claim_action_for_execution,
)
from app.automation.tenant import TargetValidator
from app.core.security import hash_password
from app.db.session import AsyncSessionLocal
from app.main import app
from app.models.automation import AIAction
from app.models.client import Client
from app.models.enums import AIActionStatus, AIActionType, MemberRole
from app.models.marketing import Campaign
from app.models.organization import Organization, OrganizationMember
from app.models.user import User
from app.publishing import ads_executor as ads_executor_module
from app.publishing.ads_executor import AdsExecutor
from app.publishing.capabilities import meta_ads_capabilities


async def _seed_org_client(*, demo_mode: bool = True):
    async with AsyncSessionLocal() as db:
        org = Organization(name="Exec Test Org", slug=f"exec-{uuid.uuid4().hex[:8]}", demo_mode=demo_mode)
        user = User(email=f"exec-{uuid.uuid4().hex[:8]}@test.com", hashed_password=hash_password("pass"), full_name="Exec")
        db.add_all([org, user])
        await db.flush()
        db.add(OrganizationMember(organization_id=org.id, user_id=user.id, role=MemberRole.owner))
        client = Client(organization_id=org.id, business_name="Exec Client", industry="saas")
        db.add(client)
        await db.commit()
        return org.id, client.id


def test_meta_capabilities_not_connected():
    matrix = meta_ads_capabilities(connected=False, credentials_configured=True)
    pause = next(c for c in matrix.capabilities if c.operation == "pause")
    assert pause.status.value == "NOT_CONNECTED"


@pytest.mark.asyncio
async def test_target_validator_rejects_foreign_client():
    async with AsyncSessionLocal() as db:
        org = Organization(name="Exec Test Org", slug=f"exec-{uuid.uuid4().hex[:8]}", demo_mode=True)
        client_a = Client(organization_id=org.id, business_name="Client A", industry="saas")
        client_b = Client(organization_id=org.id, business_name="Client B", industry="saas")
        db.add(org)
        await db.flush()
        client_a.organization_id = org.id
        client_b.organization_id = org.id
        db.add_all([client_a, client_b])
        await db.flush()

        camp = Campaign(
            organization_id=org.id,
            client_id=client_a.id,
            name="Camp A",
            platform="meta",
            status="active",
        )
        db.add(camp)
        await db.flush()

        action = AIAction(
            organization_id=org.id,
            client_id=client_b.id,
            action_type=AIActionType.pause_campaign,
            description="pause",
            reason="test",
            target_id=str(camp.id),
            status=AIActionStatus.approved,
        )
        result = await TargetValidator(db).validate_action_targets(action)
        assert result.ok is False
        assert "TENANT_MISMATCH" in result.errors
        await db.rollback()


@pytest.mark.asyncio
async def test_execution_idempotent_when_already_completed():
    org_id, client_id = await _seed_org_client()
    async with AsyncSessionLocal() as db:
        action = AIAction(
            organization_id=org_id,
            client_id=client_id,
            action_type=AIActionType.create_creative,
            description="creative",
            reason="test",
            status=AIActionStatus.completed,
            result={"confirmed": True, "demo": True},
            requires_approval=False,
        )
        db.add(action)
        await db.flush()

        out = await ExecutionEngine(db).execute(action, actor_user_id=None)
        assert out.status == AIActionStatus.completed
        assert out.result.get("confirmed") is True
        await db.rollback()


@pytest.mark.asyncio
async def test_ads_executor_meta_pause_requires_external_id(monkeypatch):
    org_id, client_id = await _seed_org_client(demo_mode=False)
    async with AsyncSessionLocal() as db:
        camp = Campaign(
            organization_id=org_id,
            client_id=client_id,
            name="Live camp",
            platform="meta",
            status="active",
        )
        db.add(camp)
        await db.flush()

        action = AIAction(
            organization_id=org_id,
            client_id=client_id,
            action_type=AIActionType.pause_campaign,
            platform="meta",
            description="pause",
            reason="test",
            target_id=str(camp.id),
            status=AIActionStatus.approved,
            demo_mode=False,
        )

        async def fake_row(*args, **kwargs):
            class Row:
                secret_ref = "enc"
                status = "connected"

            return Row()

        monkeypatch.setattr(ads_executor_module, "get_integration_row", fake_row)
        monkeypatch.setattr(ads_executor_module, "load_tokens", lambda row: {"access_token": "token"})
        monkeypatch.setattr(ads_executor_module.get_settings(), "demo_mode", False)

        result = await AdsExecutor(db).execute(action, campaign=camp)
        assert result.success is False
        assert result.error_code == "EXTERNAL_ID_REQUIRED"
        await db.rollback()


@pytest.mark.asyncio
async def test_ads_executor_meta_pause_confirmed_with_mock_transport(monkeypatch):
    org_id, client_id = await _seed_org_client(demo_mode=False)
    async with AsyncSessionLocal() as db:
        camp = Campaign(
            organization_id=org_id,
            client_id=client_id,
            name="Live camp",
            platform="meta",
            status="active",
            external_id="123456789",
        )
        db.add(camp)
        await db.flush()

        action = AIAction(
            organization_id=org_id,
            client_id=client_id,
            action_type=AIActionType.pause_campaign,
            platform="meta",
            description="pause",
            reason="test",
            target_id=str(camp.id),
            status=AIActionStatus.approved,
            demo_mode=False,
        )

        async def fake_row(*args, **kwargs):
            class Row:
                secret_ref = "enc"
                status = "connected"

            return Row()

        monkeypatch.setattr(ads_executor_module, "get_integration_row", fake_row)
        monkeypatch.setattr(ads_executor_module, "load_tokens", lambda row: {"access_token": "token"})
        monkeypatch.setattr(ads_executor_module.get_settings(), "demo_mode", False)

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"success": True})

        transport = httpx.MockTransport(handler)

        class Client(httpx.AsyncClient):
            def __init__(self, *args, **kwargs):
                kwargs["transport"] = transport
                super().__init__(*args, **kwargs)

        class Shim:
            AsyncClient = Client
            TimeoutException = httpx.TimeoutException
            HTTPError = httpx.HTTPError

        monkeypatch.setattr(ads_executor_module, "httpx", Shim)

        result = await AdsExecutor(db).execute(action, campaign=camp)
        assert result.success is True
        assert result.external_id == "123456789"
        assert camp.status == "paused"
        await db.rollback()


def test_build_action_idempotency_key_stable():
    org = uuid.uuid4()
    k1 = build_action_idempotency_key(
        organization_id=org,
        action_type="PAUSE_CAMPAIGN",
        target_id="abc",
        payload={"x": 1},
    )
    k2 = build_action_idempotency_key(
        organization_id=org,
        action_type="PAUSE_CAMPAIGN",
        target_id="abc",
        payload={"x": 1},
    )
    assert k1 == k2


def test_sanitize_platform_response_strips_tokens():
    clean = sanitize_platform_response(
        {"access_token": "secret", "nested": {"refresh_token": "x", "success": True}}
    )
    assert "access_token" not in clean
    assert "refresh_token" not in (clean.get("nested") or {})


@pytest.mark.asyncio
async def test_duplicate_idempotency_key_rejected_at_db():
    org_id, client_id = await _seed_org_client()
    key = f"action:test-dup-{uuid.uuid4().hex}"
    async with AsyncSessionLocal() as db:
        db.add(
            AIAction(
                organization_id=org_id,
                client_id=client_id,
                action_type=AIActionType.create_creative,
                description="a",
                reason="b",
                status=AIActionStatus.pending,
                idempotency_key=key,
            )
        )
        await db.commit()

    async with AsyncSessionLocal() as db:
        from app.models.enums import AIActionType as AT
        from app.schemas.autopilot import AIActionCreate
        from app.services.action_service import ActionService

        org = await db.get(Organization, org_id)
        out1 = await ActionService(db).create(
            org_id,
            AIActionCreate(
                action_type=AT.create_creative,
                client_id=client_id,
                description="dup",
                reason="dup",
                payload={"idempotency_key": key},
            ),
            user_id=None,
            organization=org,
        )
        out2 = await ActionService(db).create(
            org_id,
            AIActionCreate(
                action_type=AT.create_creative,
                client_id=client_id,
                description="dup",
                reason="dup",
                payload={"idempotency_key": key},
            ),
            user_id=None,
            organization=org,
        )
        assert out1.id == out2.id
        await db.rollback()


@pytest.mark.asyncio
async def test_execution_claim_prevents_double_execute():
    org_id, client_id = await _seed_org_client()
    async with AsyncSessionLocal() as db:
        action = AIAction(
            organization_id=org_id,
            client_id=client_id,
            action_type=AIActionType.create_creative,
            description="creative",
            reason="test",
            status=AIActionStatus.approved,
            requires_approval=True,
        )
        db.add(action)
        await db.flush()
        claim1 = await try_claim_action_for_execution(db, action)
        claim2 = await try_claim_action_for_execution(db, action)
        assert claim1 == "claimed"
        assert claim2 == "executing"
        await db.rollback()


@pytest.mark.asyncio
async def test_capabilities_endpoint():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        login = await client.post("/api/v1/auth/login", json={"email": "demo@growthos.ai", "password": "demo1234"})
        headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
        resp = await client.get("/api/v1/autopilot/capabilities", headers=headers)
        assert resp.status_code == 200
        body = resp.json()
        assert "providers" in body
        assert len(body["providers"]) >= 2
