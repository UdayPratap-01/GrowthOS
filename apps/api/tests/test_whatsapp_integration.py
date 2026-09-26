"""WhatsApp integration — WABA metadata, webhooks, capability restrictions."""

from __future__ import annotations

import hashlib
import hmac
import json
import uuid
import pytest
from httpx import ASGITransport, AsyncClient

from app.core.config import get_settings
from app.core.security import hash_password
from app.db.session import AsyncSessionLocal
from app.integrations.meta_family import WhatsAppIntegration
from app.integrations.persistence import upsert_integration
from app.main import app
from app.models.client import Client
from app.models.enums import MemberRole
from app.models.organization import Organization, OrganizationMember
from app.models.user import User
from app.publishing.capabilities import CapabilityStatus, whatsapp_capabilities


class FakeResp:
    def __init__(self, status_code: int, data: dict | None = None):
        self.status_code = status_code
        self._data = data or {}

    def json(self) -> dict:
        return self._data


def test_whatsapp_capabilities_messaging_disabled():
    matrix = whatsapp_capabilities(connected=True, credentials_configured=True)
    by_op = {c.operation: c for c in matrix.capabilities}
    assert by_op["get_business_account"].status == CapabilityStatus.supported
    assert by_op["send_message"].status == CapabilityStatus.unsupported
    assert by_op["receive_webhook"].status == CapabilityStatus.unsupported
    assert "HTTPS" in by_op["receive_webhook"].message


@pytest.mark.asyncio
async def test_whatsapp_sync_discovers_waba_metadata():
    integration = WhatsAppIntegration()
    async with AsyncSessionLocal() as db:
        org = Organization(name="WA", slug=f"wa-{uuid.uuid4().hex[:6]}", demo_mode=False)
        db.add(org)
        await db.flush()
        client_row = Client(organization_id=org.id, business_name="WA Client", industry="saas")
        db.add(client_row)
        await db.flush()
        await upsert_integration(
            db,
            organization_id=org.id,
            provider="whatsapp",
            client_id=client_row.id,
            status="connected",
            config={"account_label": "WhatsApp"},
            token_payload={"access_token": "wa-token", "provider": "whatsapp"},
        )
        await db.commit()
        org_id = org.id
        client_id = client_row.id

    class HttpClient:
        async def get(self, url, **kwargs):
            if url.endswith("/me"):
                return FakeResp(200, {"id": "user1", "name": "Biz User"})
            if url.endswith("/me/businesses"):
                return FakeResp(200, {"data": [{"id": "biz1", "name": "Biz"}]})
            if "owned_whatsapp_business_accounts" in url:
                return FakeResp(200, {"data": [{"id": "waba1", "name": "WABA", "timezone_id": "1"}]})
            if url.endswith("/phone_numbers"):
                return FakeResp(
                    200,
                    {
                        "data": [
                            {
                                "id": "phone1",
                                "display_phone_number": "+15550001",
                                "verified_name": "Support",
                                "quality_rating": "GREEN",
                                "status": "CONNECTED",
                            }
                        ]
                    },
                )
            return FakeResp(404, {})

    async with AsyncSessionLocal() as db:
        integration._db = db
        records = await integration._sync_whatsapp_metadata(
            db, org_id, client_id, "wa-token", HttpClient()
        )
        from app.integrations.persistence import get_integration_row

        row = await get_integration_row(
            db, organization_id=org_id, provider="whatsapp", client_id=client_id
        )
        cfg = row.config or {}
        assert records >= 1
        assert cfg["webhook_status"] == "PENDING_PUBLIC_HTTPS"
        assert len(cfg["whatsapp_business_accounts"]) == 1
        assert cfg["phone_numbers"][0]["display_phone_number"] == "+15550001"


@pytest.mark.asyncio
async def test_whatsapp_webhook_verify_and_ack(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "meta_app_secret", "test-secret")
    monkeypatch.setattr(settings, "meta_webhook_verify_token", "verify-me")

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        verify = await client.get(
            "/api/v1/webhooks/whatsapp",
            params={"hub.mode": "subscribe", "hub.verify_token": "verify-me", "hub.challenge": "42"},
        )
        assert verify.status_code == 200
        assert verify.json() == 42

        payload = {"object": "whatsapp_business_account", "entry": [{"id": "e1"}]}
        raw = json.dumps(payload).encode()
        sig = "sha256=" + hmac.new(b"test-secret", raw, hashlib.sha256).hexdigest()
        post = await client.post(
            "/api/v1/webhooks/whatsapp",
            content=raw,
            headers={"X-Hub-Signature-256": sig, "Content-Type": "application/json"},
        )
        assert post.status_code == 200
        body = post.json()
        assert body["messaging_enabled"] is False
        assert body["production_status"] == "PENDING_PUBLIC_HTTPS"


@pytest.mark.asyncio
async def test_whatsapp_status_endpoint():
    email = f"wa-{uuid.uuid4().hex[:6]}@t.com"
    async with AsyncSessionLocal() as db:
        org = Organization(name="WA2", slug=f"wa2-{uuid.uuid4().hex[:6]}", demo_mode=False)
        user = User(email=email, hashed_password=hash_password("pass"), full_name="WA")
        db.add_all([org, user])
        await db.flush()
        db.add(OrganizationMember(organization_id=org.id, user_id=user.id, role=MemberRole.owner))
        await upsert_integration(
            db,
            organization_id=org.id,
            provider="whatsapp",
            client_id=None,
            status="connected",
            config={
                "webhook_status": "PENDING_PUBLIC_HTTPS",
                "phone_numbers": [{"display_phone_number": "+15550001"}],
            },
            token_payload={"access_token": "tok", "provider": "whatsapp"},
        )
        await db.commit()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        login = await client.post("/api/v1/auth/login", json={"email": email, "password": "pass"})
        headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
        resp = await client.get("/api/v1/seo/integrations/whatsapp/status", headers=headers)
        assert resp.status_code == 200
        body = resp.json()
        assert body["messaging_enabled"] is False
        assert body["webhook_status"] == "PENDING_PUBLIC_HTTPS"
        assert "access_token" not in json.dumps(body)
