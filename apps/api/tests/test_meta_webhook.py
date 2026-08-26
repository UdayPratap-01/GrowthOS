"""P0-6 — Meta lead webhook must persist leads, not silently drop them."""

from __future__ import annotations

import hashlib
import hmac
import json
import uuid

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.core.config import get_settings
from app.db.session import AsyncSessionLocal
from app.main import app
from app.models.ai_ops import Integration
from app.models.client import Client
from app.models.leads import Lead
from app.models.organization import Organization
from app.models.webhooks import WebhookEvent

APP_SECRET = "test-meta-app-secret"


def _sign(body: bytes) -> str:
    return "sha256=" + hmac.new(APP_SECRET.encode(), body, hashlib.sha256).hexdigest()


def _payload(leadgen_id: str, page_id: str) -> dict:
    return {
        "object": "page",
        "entry": [
            {
                "id": page_id,
                "time": 1700000000,
                "changes": [
                    {
                        "field": "leadgen",
                        "value": {
                            "leadgen_id": leadgen_id,
                            "page_id": page_id,
                            "form_id": "form-777",
                            "ad_id": "ad-123",
                            "campaign_id": "camp-456",
                            "created_time": 1700000000,
                        },
                    }
                ],
            }
        ],
    }


@pytest.fixture(autouse=True)
def _app_secret(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "meta_app_secret", APP_SECRET, raising=False)
    yield


async def _seed_meta_integration(page_id: str) -> tuple[uuid.UUID, uuid.UUID]:
    """Create an org + client + connected Meta integration bound to `page_id`."""
    async with AsyncSessionLocal() as db:
        org = Organization(name=f"Webhook Org {page_id}", slug=f"webhook-org-{page_id}", demo_mode=False)
        db.add(org)
        await db.flush()
        client = Client(organization_id=org.id, business_name="Webhook Client", industry="saas")
        db.add(client)
        await db.flush()
        db.add(
            Integration(
                organization_id=org.id,
                client_id=client.id,
                provider="meta",
                status="connected",
                config={"page_id": page_id},
            )
        )
        await db.commit()
        return org.id, client.id


async def _post(client: AsyncClient, payload: dict, *, signature: str | None = None):
    body = json.dumps(payload).encode()
    return await client.post(
        "/api/v1/webhooks/meta",
        content=body,
        headers={"X-Hub-Signature-256": signature or _sign(body), "Content-Type": "application/json"},
    )


@pytest.mark.asyncio
async def test_valid_webhook_creates_lead():
    page_id = f"page-{uuid.uuid4().hex[:8]}"
    leadgen_id = f"lead-{uuid.uuid4().hex[:8]}"
    org_id, client_id = await _seed_meta_integration(page_id)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as http:
        resp = await _post(http, _payload(leadgen_id, page_id))

    assert resp.status_code == 200, resp.text
    assert resp.json()["leads_created"] == 1

    async with AsyncSessionLocal() as db:
        lead = await db.scalar(select(Lead).where(Lead.external_id == leadgen_id))
        assert lead is not None, "webhook must persist the lead"
        assert lead.organization_id == org_id
        assert lead.client_id == client_id
        assert lead.source == "meta_lead_ads"
        assert lead.campaign == "camp-456"
        assert lead.ad == "ad-123"
        assert lead.source_metadata["platform"] == "meta"
        assert lead.source_metadata["form_id"] == "form-777"

        event = await db.scalar(select(WebhookEvent).where(WebhookEvent.event_id == leadgen_id))
        assert event is not None and event.status == "processed"
        assert event.lead_id == lead.id


@pytest.mark.asyncio
async def test_lead_without_graph_token_is_stored_with_explicit_limitations():
    """No access token means no contact details. Record the gap, never invent a person."""
    page_id = f"page-{uuid.uuid4().hex[:8]}"
    leadgen_id = f"lead-{uuid.uuid4().hex[:8]}"
    await _seed_meta_integration(page_id)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as http:
        await _post(http, _payload(leadgen_id, page_id))

    async with AsyncSessionLocal() as db:
        lead = await db.scalar(select(Lead).where(Lead.external_id == leadgen_id))
        assert lead.email is None
        assert lead.phone is None
        assert "Unidentified" in lead.name
        assert lead.source_metadata["contact_details_available"] is False
        assert lead.source_metadata["data_limitations"]


@pytest.mark.asyncio
async def test_invalid_signature_is_rejected_and_stores_nothing():
    page_id = f"page-{uuid.uuid4().hex[:8]}"
    leadgen_id = f"lead-{uuid.uuid4().hex[:8]}"
    await _seed_meta_integration(page_id)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as http:
        resp = await _post(http, _payload(leadgen_id, page_id), signature="sha256=deadbeef")

    assert resp.status_code == 401
    async with AsyncSessionLocal() as db:
        assert await db.scalar(select(Lead).where(Lead.external_id == leadgen_id)) is None
        assert await db.scalar(select(WebhookEvent).where(WebhookEvent.event_id == leadgen_id)) is None


@pytest.mark.asyncio
async def test_duplicate_delivery_does_not_create_second_lead():
    page_id = f"page-{uuid.uuid4().hex[:8]}"
    leadgen_id = f"lead-{uuid.uuid4().hex[:8]}"
    await _seed_meta_integration(page_id)
    payload = _payload(leadgen_id, page_id)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as http:
        first = await _post(http, payload)
        second = await _post(http, payload)
        third = await _post(http, payload)

    assert first.json()["leads_created"] == 1
    assert second.json()["leads_created"] == 0
    assert second.json()["duplicates_ignored"] == 1
    assert third.json()["duplicates_ignored"] == 1

    async with AsyncSessionLocal() as db:
        leads = (await db.execute(select(Lead).where(Lead.external_id == leadgen_id))).scalars().all()
        assert len(leads) == 1, "Meta retries must not duplicate leads"
        events = (
            await db.execute(select(WebhookEvent).where(WebhookEvent.event_id == leadgen_id))
        ).scalars().all()
        assert len(events) == 1


@pytest.mark.asyncio
async def test_unknown_integration_is_retained_not_dropped():
    """An event for an unconnected page must be stored for replay, not discarded."""
    leadgen_id = f"lead-{uuid.uuid4().hex[:8]}"
    payload = _payload(leadgen_id, f"page-unknown-{uuid.uuid4().hex[:8]}")

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as http:
        resp = await _post(http, payload)

    assert resp.status_code == 200
    assert resp.json()["leads_created"] == 0
    assert resp.json()["unroutable"] == 1

    async with AsyncSessionLocal() as db:
        event = await db.scalar(select(WebhookEvent).where(WebhookEvent.event_id == leadgen_id))
        assert event is not None, "raw event must be retained so no data is lost"
        assert event.status == "unroutable"
        assert event.payload["leadgen_id"] == leadgen_id
        assert await db.scalar(select(Lead).where(Lead.external_id == leadgen_id)) is None


@pytest.mark.asyncio
async def test_malformed_payloads_return_400():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as http:
        for bad in (
            {"object": "user", "entry": []},
            {"object": "page", "entry": "not-a-list"},
            {"object": "page", "entry": [{"id": "p", "changes": [{"field": "leadgen", "value": {}}]}]},
            ["not", "an", "object"],
        ):
            resp = await _post(http, bad)
            assert resp.status_code == 400, f"{bad} should be rejected as malformed"

        body = b"{not json"
        resp = await http.post(
            "/api/v1/webhooks/meta",
            content=body,
            headers={"X-Hub-Signature-256": _sign(body), "Content-Type": "application/json"},
        )
        assert resp.status_code == 400


@pytest.mark.asyncio
async def test_non_leadgen_changes_are_ignored_without_error():
    page_id = f"page-{uuid.uuid4().hex[:8]}"
    await _seed_meta_integration(page_id)
    payload = {
        "object": "page",
        "entry": [{"id": page_id, "changes": [{"field": "feed", "value": {"item": "comment"}}]}],
    }

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as http:
        resp = await _post(http, payload)

    assert resp.status_code == 200
    assert resp.json()["leads_created"] == 0


@pytest.mark.asyncio
async def test_database_failure_returns_500_so_meta_retries(monkeypatch):
    page_id = f"page-{uuid.uuid4().hex[:8]}"
    leadgen_id = f"lead-{uuid.uuid4().hex[:8]}"
    await _seed_meta_integration(page_id)

    from app.services import lead_ingest_service

    async def _boom(*_args, **_kwargs):
        raise RuntimeError("database is down")

    monkeypatch.setattr(lead_ingest_service, "_upsert_lead", _boom)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as http:
        resp = await _post(http, _payload(leadgen_id, page_id))

    assert resp.status_code == 500, "a persistence failure must not be reported as success"

    async with AsyncSessionLocal() as db:
        assert await db.scalar(select(Lead).where(Lead.external_id == leadgen_id)) is None


@pytest.mark.asyncio
async def test_graph_api_enrichment_populates_contact_details():
    """When a token exists and the Graph API answers, real values are stored."""
    from app.services import lead_ingest_service

    page_id = f"page-{uuid.uuid4().hex[:8]}"
    leadgen_id = f"lead-{uuid.uuid4().hex[:8]}"
    org_id, client_id = await _seed_meta_integration(page_id)

    async def fake_fetch(_leadgen_id: str, _token: str) -> dict:
        return {
            "field_data": [
                {"name": "full_name", "values": ["Dana Whitfield"]},
                {"name": "email", "values": ["dana@example.com"]},
                {"name": "phone_number", "values": ["+15551234567"]},
            ]
        }

    async with AsyncSessionLocal() as db:
        integration = await db.scalar(
            select(Integration).where(Integration.organization_id == org_id, Integration.provider == "meta")
        )
        from app.security.secrets import get_secret_store

        integration.secret_ref = get_secret_store().store(json.dumps({"page_access_token": "tok-abc"}))
        await db.commit()

        outcome = await lead_ingest_service.ingest_meta_webhook(
            db, _payload(leadgen_id, page_id), lead_fetcher=fake_fetch
        )

    assert outcome.processed == 1
    async with AsyncSessionLocal() as db:
        lead = await db.scalar(select(Lead).where(Lead.external_id == leadgen_id))
        assert lead.name == "Dana Whitfield"
        assert lead.email == "dana@example.com"
        assert lead.phone == "+15551234567"
        assert lead.source_metadata["contact_details_available"] is True
        assert "data_limitations" not in lead.source_metadata
