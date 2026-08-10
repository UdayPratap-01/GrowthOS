"""
Meta webhook routing must be decided by verified ownership, never by "whichever
integration matched first".

A leadgen webhook arrives with a page id and no tenant information at all, so
the page-to-organization mapping is the only thing standing between one
customer's prospects and another customer's CRM. Where that mapping is not
unambiguous, the event is quarantined rather than filed under a guess.
"""

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
from app.services.lead_ingest_service import (
    AmbiguousPageRoutingError,
    resolve_integration,
)

APP_SECRET = "test-meta-app-secret"


@pytest.fixture(autouse=True)
def _app_secret(monkeypatch):
    monkeypatch.setattr(get_settings(), "meta_app_secret", APP_SECRET, raising=False)
    yield


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
                            "form_id": "form-1",
                            "created_time": 1700000000,
                        },
                    }
                ],
            }
        ],
    }


async def _post(http: AsyncClient, payload: dict):
    body = json.dumps(payload).encode()
    return await http.post(
        "/api/v1/webhooks/meta",
        content=body,
        headers={"X-Hub-Signature-256": _sign(body), "Content-Type": "application/json"},
    )


async def _seed_org(label: str, *, config: dict, status: str = "connected"):
    """An organization with a client and one Meta integration holding `config`."""
    suffix = uuid.uuid4().hex[:8]
    async with AsyncSessionLocal() as db:
        org = Organization(name=f"{label} {suffix}", slug=f"{label}-{suffix}", demo_mode=False)
        db.add(org)
        await db.flush()
        client = Client(organization_id=org.id, business_name=f"{label} Client", industry="saas")
        db.add(client)
        await db.flush()
        db.add(
            Integration(
                organization_id=org.id,
                client_id=client.id,
                provider="meta",
                status=status,
                config=config,
            )
        )
        await db.commit()
        return org.id, client.id


def _client() -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


# --------------------------------------------------------------------------
# Correct attribution
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_each_page_routes_to_its_own_organization():
    page_a = f"page-a-{uuid.uuid4().hex[:8]}"
    page_b = f"page-b-{uuid.uuid4().hex[:8]}"
    org_a, client_a = await _seed_org("routing-a", config={"page_id": page_a})
    org_b, client_b = await _seed_org("routing-b", config={"page_id": page_b})

    lead_a = f"lead-{uuid.uuid4().hex[:8]}"
    lead_b = f"lead-{uuid.uuid4().hex[:8]}"

    async with _client() as http:
        first = await _post(http, _payload(lead_a, page_a))
        second = await _post(http, _payload(lead_b, page_b))

    assert first.json()["leads_created"] == 1, first.text
    assert second.json()["leads_created"] == 1, second.text

    async with AsyncSessionLocal() as db:
        a = await db.scalar(select(Lead).where(Lead.external_id == lead_a))
        b = await db.scalar(select(Lead).where(Lead.external_id == lead_b))

    assert (a.organization_id, a.client_id) == (org_a, client_a)
    assert (b.organization_id, b.client_id) == (org_b, client_b)
    assert a.organization_id != b.organization_id


@pytest.mark.asyncio
async def test_a_page_listed_in_page_ids_still_routes():
    """Multi-page accounts record a list; it must be matched the same way."""
    page = f"page-list-{uuid.uuid4().hex[:8]}"
    org_id, _ = await _seed_org("routing-list", config={"page_ids": [page, "other-page"]})
    leadgen_id = f"lead-{uuid.uuid4().hex[:8]}"

    async with _client() as http:
        resp = await _post(http, _payload(leadgen_id, page))

    assert resp.json()["leads_created"] == 1, resp.text
    async with AsyncSessionLocal() as db:
        lead = await db.scalar(select(Lead).where(Lead.external_id == leadgen_id))
    assert lead.organization_id == org_id


# --------------------------------------------------------------------------
# Refusal
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_an_unknown_page_creates_no_lead_anywhere():
    await _seed_org("routing-bystander", config={"page_id": f"page-{uuid.uuid4().hex[:8]}"})
    leadgen_id = f"lead-{uuid.uuid4().hex[:8]}"
    unknown = f"page-unknown-{uuid.uuid4().hex[:8]}"

    async with _client() as http:
        resp = await _post(http, _payload(leadgen_id, unknown))

    assert resp.status_code == 200, resp.text
    assert resp.json()["leads_created"] == 0
    assert resp.json()["unroutable"] == 1

    async with AsyncSessionLocal() as db:
        assert await db.scalar(select(Lead).where(Lead.external_id == leadgen_id)) is None
        event = await db.scalar(select(WebhookEvent).where(WebhookEvent.event_id == leadgen_id))
    assert event is not None and event.status == "unroutable", "the payload must be retained"
    assert event.organization_id is None


@pytest.mark.asyncio
async def test_two_organizations_claiming_one_page_fails_closed():
    """
    The finding: with a first-match lookup, whichever row the database returned
    first won the lead. Now nobody does.
    """
    page = f"page-clash-{uuid.uuid4().hex[:8]}"
    org_a, _ = await _seed_org("clash-a", config={"page_id": page})
    org_b, _ = await _seed_org("clash-b", config={"page_id": page})
    leadgen_id = f"lead-{uuid.uuid4().hex[:8]}"

    async with _client() as http:
        resp = await _post(http, _payload(leadgen_id, page))

    assert resp.status_code == 200, resp.text
    assert resp.json()["leads_created"] == 0
    assert resp.json()["ambiguous"] == 1

    async with AsyncSessionLocal() as db:
        assert await db.scalar(select(Lead).where(Lead.external_id == leadgen_id)) is None
        event = await db.scalar(select(WebhookEvent).where(WebhookEvent.event_id == leadgen_id))

    assert event is not None and event.status == "ambiguous"
    assert event.organization_id is None, "no tenant may be guessed"
    assert event.payload, "the raw event must be kept for replay after the clash is resolved"
    assert str(org_a) in event.error or str(org_b) in event.error or "2 Meta integrations" in event.error


@pytest.mark.asyncio
async def test_a_clash_within_one_organization_also_fails_closed():
    """Two clients of the same agency cannot share a page either."""
    page = f"page-inner-{uuid.uuid4().hex[:8]}"
    suffix = uuid.uuid4().hex[:8]
    async with AsyncSessionLocal() as db:
        org = Organization(name=f"Inner {suffix}", slug=f"inner-{suffix}", demo_mode=False)
        db.add(org)
        await db.flush()
        first = Client(organization_id=org.id, business_name="First", industry="saas")
        second = Client(organization_id=org.id, business_name="Second", industry="saas")
        db.add_all([first, second])
        await db.flush()
        db.add_all(
            [
                Integration(organization_id=org.id, client_id=first.id, provider="meta",
                            status="connected", config={"page_id": page}),
                Integration(organization_id=org.id, client_id=second.id, provider="meta",
                            status="connected", config={"page_id": page}),
            ]
        )
        await db.commit()

    leadgen_id = f"lead-{uuid.uuid4().hex[:8]}"
    async with _client() as http:
        resp = await _post(http, _payload(leadgen_id, page))

    assert resp.json()["ambiguous"] == 1, resp.text
    async with AsyncSessionLocal() as db:
        assert await db.scalar(select(Lead).where(Lead.external_id == leadgen_id)) is None


@pytest.mark.asyncio
async def test_a_disconnected_neighbour_still_makes_the_mapping_ambiguous():
    """
    A stale row claiming the page is exactly the situation where guessing is
    tempting and wrong: it may be the tenant that is mid-migration.
    """
    page = f"page-stale-{uuid.uuid4().hex[:8]}"
    await _seed_org("stale-a", config={"page_id": page}, status="not_connected")
    await _seed_org("stale-b", config={"page_id": page})
    leadgen_id = f"lead-{uuid.uuid4().hex[:8]}"

    async with _client() as http:
        resp = await _post(http, _payload(leadgen_id, page))

    assert resp.json()["ambiguous"] == 1, resp.text


@pytest.mark.asyncio
async def test_an_ambiguous_event_is_not_reprocessed_on_redelivery():
    page = f"page-redeliver-{uuid.uuid4().hex[:8]}"
    await _seed_org("redeliver-a", config={"page_id": page})
    await _seed_org("redeliver-b", config={"page_id": page})
    leadgen_id = f"lead-{uuid.uuid4().hex[:8]}"
    payload = _payload(leadgen_id, page)

    async with _client() as http:
        first = await _post(http, payload)
        second = await _post(http, payload)

    assert first.json()["ambiguous"] == 1
    assert second.json()["duplicates_ignored"] == 1, second.text

    async with AsyncSessionLocal() as db:
        events = list(
            await db.scalars(select(WebhookEvent).where(WebhookEvent.event_id == leadgen_id))
        )
    assert len(events) == 1


@pytest.mark.asyncio
async def test_an_event_with_no_page_id_is_not_attributed():
    await _seed_org("nopage", config={"page_id": f"page-{uuid.uuid4().hex[:8]}"})
    leadgen_id = f"lead-{uuid.uuid4().hex[:8]}"
    payload = {
        "object": "page",
        "entry": [{"changes": [{"field": "leadgen", "value": {"leadgen_id": leadgen_id}}]}],
    }

    async with _client() as http:
        resp = await _post(http, payload)

    assert resp.json()["leads_created"] == 0
    async with AsyncSessionLocal() as db:
        assert await db.scalar(select(Lead).where(Lead.external_id == leadgen_id)) is None


# --------------------------------------------------------------------------
# The resolver itself
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resolver_returns_the_single_owner():
    page = f"page-single-{uuid.uuid4().hex[:8]}"
    org_id, _ = await _seed_org("resolve-one", config={"external_account_id": page})
    async with AsyncSessionLocal() as db:
        found = await resolve_integration(db, page_id=page)
    assert found is not None and found.organization_id == org_id


@pytest.mark.asyncio
async def test_resolver_raises_rather_than_choosing():
    page = f"page-two-{uuid.uuid4().hex[:8]}"
    await _seed_org("resolve-a", config={"page_id": page})
    await _seed_org("resolve-b", config={"page_id": page})
    async with AsyncSessionLocal() as db:
        with pytest.raises(AmbiguousPageRoutingError):
            await resolve_integration(db, page_id=page)


@pytest.mark.asyncio
async def test_connecting_a_page_another_tenant_already_owns_is_refused():
    """
    Catch the clash where it can still be explained to someone, instead of at
    3am when a lead is quarantined.
    """
    from fastapi import HTTPException

    from app.integrations.persistence import upsert_integration

    page = f"page-connect-{uuid.uuid4().hex[:8]}"
    await _seed_org("connect-owner", config={"page_id": page})

    suffix = uuid.uuid4().hex[:8]
    async with AsyncSessionLocal() as db:
        newcomer = Organization(name=f"Newcomer {suffix}", slug=f"newcomer-{suffix}", demo_mode=False)
        db.add(newcomer)
        await db.flush()
        with pytest.raises(HTTPException) as caught:
            await upsert_integration(
                db,
                organization_id=newcomer.id,
                provider="meta",
                client_id=None,
                status="connected",
                config={"page_id": page},
            )
        await db.rollback()

    assert caught.value.status_code == 409
    assert "PAGE_ALREADY_CONNECTED" in str(caught.value.detail)


@pytest.mark.asyncio
async def test_reconnecting_your_own_page_is_still_allowed():
    page = f"page-reconnect-{uuid.uuid4().hex[:8]}"
    org_id, client_id = await _seed_org("reconnect", config={"page_id": page})

    from app.integrations.persistence import upsert_integration

    async with AsyncSessionLocal() as db:
        row = await upsert_integration(
            db,
            organization_id=org_id,
            provider="meta",
            client_id=client_id,
            status="connected",
            config={"page_id": page, "account_label": "Refreshed"},
        )
        await db.commit()
    assert row.config["account_label"] == "Refreshed"
