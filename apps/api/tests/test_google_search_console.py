"""Google Search Console integration — read-only, mocked API."""

from __future__ import annotations

import json
import uuid
from unittest.mock import patch

import pytest
from httpx import ASGITransport, AsyncClient
from app.core.config import get_settings
from app.core.security import hash_password
from app.db.session import AsyncSessionLocal
from app.integrations.google_search_console import GoogleSearchConsoleIntegration
from app.integrations.oauth import encode_oauth_state
from app.integrations.persistence import get_integration_row, load_tokens, upsert_integration
from app.main import app
from app.models.enums import MemberRole
from app.models.organization import Organization, OrganizationMember
from app.models.user import User
from app.publishing.capabilities import CapabilityStatus, google_search_console_capabilities


def test_gsc_capabilities_not_configured():
    matrix = google_search_console_capabilities(connected=False, credentials_configured=False)
    assert matrix.provider == "google_search_console"
    ops = {c.operation: c.status for c in matrix.capabilities}
    assert ops["list_sites"] == CapabilityStatus.not_configured


def test_gsc_capabilities_connected_read_only():
    matrix = google_search_console_capabilities(connected=True, credentials_configured=True)
    by_op = {c.operation: c for c in matrix.capabilities}
    assert by_op["search_analytics"].status == CapabilityStatus.supported
    assert by_op["url_inspection"].status == CapabilityStatus.unsupported


@pytest.mark.asyncio
async def test_gsc_oauth_callback_persists_sites(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "google_client_id", "gid")
    monkeypatch.setattr(settings, "google_client_secret", "gsec")

    async def fake_exchange(*, code, redirect_uri):
        assert code == "auth-code"
        return {"access_token": "at", "refresh_token": "rt", "expires_in": 3600}

    async def fake_sites(access_token):
        assert access_token == "at"
        return [{"siteUrl": "https://example.com/", "permissionLevel": "siteOwner"}]

    integration = GoogleSearchConsoleIntegration()
    async with AsyncSessionLocal() as db:
        integration._db = db
        org = Organization(name="GSC Org", slug=f"gsc-{uuid.uuid4().hex[:8]}", demo_mode=False)
        db.add(org)
        await db.flush()
        state = encode_oauth_state(
            provider="google_search_console",
            organization_id=org.id,
            client_id=None,
            user_id=uuid.uuid4(),
        )
        with patch.object(integration, "_list_sites", fake_sites):
            with patch("app.integrations.google_search_console.exchange_code", fake_exchange):
                result = await integration.handle_callback(code="auth-code", state=state)
        await db.commit()
        org_id = org.id

    assert result["site_count"] == 1
    async with AsyncSessionLocal() as db:
        stored = await get_integration_row(
            db, organization_id=org_id, provider="google_search_console", client_id=None
        )
        assert stored is not None
        assert stored.config["site_url"] == "https://example.com/"
        tokens = load_tokens(stored)
        assert tokens["access_token"] == "at"
        assert "refresh_token" not in json.dumps(stored.config)


@pytest.mark.asyncio
async def test_gsc_analytics_endpoint_tenant_isolation():
    email_a = f"a-{uuid.uuid4().hex[:6]}@t.com"
    email_b = f"b-{uuid.uuid4().hex[:6]}@t.com"
    async with AsyncSessionLocal() as db:
        org_a = Organization(name="A", slug=f"a-{uuid.uuid4().hex[:6]}", demo_mode=False)
        org_b = Organization(name="B", slug=f"b-{uuid.uuid4().hex[:6]}", demo_mode=False)
        user_a = User(email=email_a, hashed_password=hash_password("pass"), full_name="A")
        user_b = User(email=email_b, hashed_password=hash_password("pass"), full_name="B")
        db.add_all([org_a, org_b, user_a, user_b])
        await db.flush()
        db.add(OrganizationMember(organization_id=org_a.id, user_id=user_a.id, role=MemberRole.owner))
        db.add(OrganizationMember(organization_id=org_b.id, user_id=user_b.id, role=MemberRole.owner))
        await upsert_integration(
            db,
            organization_id=org_a.id,
            provider="google_search_console",
            client_id=None,
            status="connected",
            config={
                "site_url": "https://tenant-a.example/",
                "last_search_analytics": {"rows": [{"query": "secret", "clicks": 1}]},
            },
            token_payload={"access_token": "tok-a", "provider": "google_search_console"},
        )
        await db.commit()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        login_b = await client.post("/api/v1/auth/login", json={"email": email_b, "password": "pass"})
        headers_b = {"Authorization": f"Bearer {login_b.json()['access_token']}"}
        denied = await client.get("/api/v1/seo/integrations/google_search_console/analytics", headers=headers_b)
        assert denied.status_code == 404

        login_a = await client.post("/api/v1/auth/login", json={"email": email_a, "password": "pass"})
        headers_a = {"Authorization": f"Bearer {login_a.json()['access_token']}"}
        ok = await client.get("/api/v1/seo/integrations/google_search_console/analytics", headers=headers_a)
        assert ok.status_code == 200
        assert ok.json()["site_url"] == "https://tenant-a.example/"
        assert "access_token" not in json.dumps(ok.json())
