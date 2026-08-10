"""P1-7 — retrieve Meta contact details later, or say they are missing. Never invent them."""

from __future__ import annotations

import json
import uuid

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.core.security import hash_password
from app.db.session import AsyncSessionLocal
from app.main import app
from app.models.ai_ops import Integration
from app.models.automation import BackgroundJob
from app.models.client import Client
from app.models.enums import LeadStatus, MemberRole
from app.models.leads import Lead, LeadActivity
from app.models.organization import Organization, OrganizationMember
from app.jobs.registry import LEAD_BACKFILL
from app.models.user import User
from app.services.lead_backfill_service import (
    COMPLETE,
    PENDING,
    PLACEHOLDER_PREFIX,
    UNAVAILABLE,
    BackfillUnavailable,
    backfill_lead_contact,
    enqueue_backfill,
    leads_awaiting_contact,
    needs_backfill,
)

PASSWORD = "Str0ng-Test-Passw0rd!"


def graph_response(**fields) -> dict:
    return {
        "id": "lead-1",
        "field_data": [{"name": name, "values": [value]} for name, value in fields.items()],
    }


def fetcher_returning(payload):
    async def _fetch(leadgen_id: str, token: str):
        return payload

    return _fetch


def fetcher_raising(exc: Exception):
    async def _fetch(leadgen_id: str, token: str):
        raise exc

    return _fetch


@pytest.fixture
async def tenant():
    """An org with a Meta integration that has a stored page access token."""
    suffix = uuid.uuid4().hex[:8]
    async with AsyncSessionLocal() as db:
        org = Organization(name=f"Backfill {suffix}", slug=f"backfill-{suffix}", demo_mode=False)
        db.add(org)
        await db.flush()
        client = Client(organization_id=org.id, business_name="Backfill Co", industry="saas")
        user = User(
            email=f"backfill-{suffix}@example.com",
            hashed_password=hash_password(PASSWORD),
            full_name="Backfill",
        )
        db.add_all([client, user])
        await db.flush()
        db.add(OrganizationMember(organization_id=org.id, user_id=user.id, role=MemberRole.owner))

        from app.security.secrets import get_secret_store

        secret_ref = get_secret_store().store(json.dumps({"page_access_token": "EAAG-token"}))
        db.add(
            Integration(
                organization_id=org.id,
                client_id=client.id,
                provider="meta",
                status="connected",
                config={"page_id": f"page-{suffix}"},
                secret_ref=secret_ref,
            )
        )
        await db.commit()
        return {
            "org_id": org.id,
            "client_id": client.id,
            "page_id": f"page-{suffix}",
            "email": user.email,
        }


async def make_lead(tenant, **overrides) -> uuid.UUID:
    """A lead as the webhook leaves it: identifiers only, no way to contact."""
    leadgen_id = overrides.pop("leadgen_id", f"lead-{uuid.uuid4().hex[:8]}")
    fields = {
        "organization_id": tenant["org_id"],
        "client_id": tenant["client_id"],
        "name": f"{PLACEHOLDER_PREFIX} {leadgen_id}",
        "email": None,
        "phone": None,
        "source": "meta_lead_ads",
        "external_id": leadgen_id,
        "source_metadata": {
            "platform": "meta",
            "page_id": tenant["page_id"],
            "leadgen_id": leadgen_id,
            "enrichment_status": UNAVAILABLE,
        },
        "status": LeadStatus.new,
    }
    fields.update(overrides)
    async with AsyncSessionLocal() as db:
        lead = Lead(**fields)
        db.add(lead)
        await db.commit()
        return lead.id


async def reload(lead_id) -> Lead:
    async with AsyncSessionLocal() as db:
        return await db.get(Lead, lead_id)


# --------------------------------------------------------------------------
# Nothing is invented
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_failed_lookup_leaves_the_contact_fields_empty(tenant):
    lead_id = await make_lead(tenant)

    async with AsyncSessionLocal() as db:
        with pytest.raises(RuntimeError):
            await backfill_lead_contact(
                db, lead_id, fetcher=fetcher_raising(RuntimeError("Graph API 500"))
            )
        await db.commit()

    lead = await reload(lead_id)
    assert lead.email is None
    assert lead.phone is None
    assert lead.name.startswith(PLACEHOLDER_PREFIX)


@pytest.mark.asyncio
async def test_a_failed_lookup_states_what_is_missing(tenant):
    lead_id = await make_lead(tenant)

    async with AsyncSessionLocal() as db:
        with pytest.raises(RuntimeError):
            await backfill_lead_contact(db, lead_id, fetcher=fetcher_raising(RuntimeError("boom")))
        await db.commit()

    metadata = (await reload(lead_id)).source_metadata
    assert metadata["enrichment_status"] == "failed"
    assert metadata["contact_details_available"] is False
    limitations = " ".join(metadata["data_limitations"]).lower()
    assert "email address" in limitations
    assert "phone number" in limitations
    assert "name" in limitations


@pytest.mark.asyncio
async def test_an_empty_graph_response_does_not_produce_a_contact(tenant):
    """The API answering with no field data is not permission to make one up."""
    lead_id = await make_lead(tenant)

    async with AsyncSessionLocal() as db:
        result = await backfill_lead_contact(db, lead_id, fetcher=fetcher_returning({"id": "x"}))
        await db.commit()

    lead = await reload(lead_id)
    assert result["status"] == PENDING
    assert lead.email is None and lead.phone is None
    assert lead.name.startswith(PLACEHOLDER_PREFIX)
    assert lead.source_metadata["data_limitations"]


@pytest.mark.asyncio
async def test_missing_token_is_recorded_rather_than_retried_forever(tenant):
    """No token is a configuration problem; retrying cannot fix it."""
    async with AsyncSessionLocal() as db:
        integration = await db.scalar(
            select(Integration).where(Integration.organization_id == tenant["org_id"])
        )
        integration.secret_ref = None
        await db.commit()

    lead_id = await make_lead(tenant)
    async with AsyncSessionLocal() as db:
        result = await backfill_lead_contact(db, lead_id, fetcher=fetcher_returning(graph_response()))
        await db.commit()

    assert result["reason"] == "no_access_token"
    metadata = (await reload(lead_id)).source_metadata
    assert metadata["enrichment_status"] == UNAVAILABLE
    assert "access token" in " ".join(metadata["data_limitations"]).lower()


# --------------------------------------------------------------------------
# A successful retrieval
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_successful_backfill_fills_the_real_values(tenant):
    lead_id = await make_lead(tenant)

    async with AsyncSessionLocal() as db:
        result = await backfill_lead_contact(
            db,
            lead_id,
            fetcher=fetcher_returning(
                graph_response(full_name="Ada Lovelace", email="ada@example.com", phone_number="+15551234")
            ),
        )
        await db.commit()

    lead = await reload(lead_id)
    assert lead.name == "Ada Lovelace"
    assert lead.email == "ada@example.com"
    assert lead.phone == "+15551234"
    assert result["status"] == COMPLETE
    assert set(result["updated_fields"]) == {"name", "email", "phone"}


@pytest.mark.asyncio
async def test_a_completed_lead_carries_no_limitations(tenant):
    lead_id = await make_lead(tenant)
    async with AsyncSessionLocal() as db:
        await backfill_lead_contact(
            db,
            lead_id,
            fetcher=fetcher_returning(graph_response(full_name="Ada", email="ada@example.com")),
        )
        await db.commit()

    metadata = (await reload(lead_id)).source_metadata
    assert metadata["enrichment_status"] == COMPLETE
    assert metadata["contact_details_available"] is True
    assert "data_limitations" not in metadata
    assert "enrichment_error" not in metadata


@pytest.mark.asyncio
async def test_partial_data_stays_partial_and_says_so(tenant):
    """An email but no phone is progress, not completion of the phone field."""
    lead_id = await make_lead(tenant)
    async with AsyncSessionLocal() as db:
        await backfill_lead_contact(
            db, lead_id, fetcher=fetcher_returning(graph_response(email="ada@example.com"))
        )
        await db.commit()

    lead = await reload(lead_id)
    assert lead.email == "ada@example.com"
    assert lead.phone is None
    assert lead.name.startswith(PLACEHOLDER_PREFIX)
    limitations = " ".join(lead.source_metadata["data_limitations"]).lower()
    assert "phone number" in limitations
    assert "email address" not in limitations


@pytest.mark.asyncio
async def test_first_and_last_name_are_combined(tenant):
    lead_id = await make_lead(tenant)
    async with AsyncSessionLocal() as db:
        await backfill_lead_contact(
            db,
            lead_id,
            fetcher=fetcher_returning(
                graph_response(first_name="Ada", last_name="Lovelace", email="ada@example.com")
            ),
        )
        await db.commit()
    assert (await reload(lead_id)).name == "Ada Lovelace"


@pytest.mark.asyncio
async def test_backfill_records_an_activity(tenant):
    lead_id = await make_lead(tenant)
    async with AsyncSessionLocal() as db:
        await backfill_lead_contact(
            db, lead_id, fetcher=fetcher_returning(graph_response(email="ada@example.com"))
        )
        await db.commit()

    async with AsyncSessionLocal() as db:
        activities = list(
            await db.scalars(select(LeadActivity).where(LeadActivity.lead_id == lead_id))
        )
    assert any(a.activity_type == "contact_backfilled" for a in activities)


# --------------------------------------------------------------------------
# Retrieval never destroys information
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_human_corrected_name_is_not_overwritten(tenant):
    lead_id = await make_lead(tenant, name="Ada L. (verified by phone)")

    async with AsyncSessionLocal() as db:
        await backfill_lead_contact(
            db,
            lead_id,
            fetcher=fetcher_returning(graph_response(full_name="ada.l", email="ada@example.com")),
        )
        await db.commit()

    assert (await reload(lead_id)).name == "Ada L. (verified by phone)"


@pytest.mark.asyncio
async def test_an_existing_email_is_not_replaced(tenant):
    lead_id = await make_lead(tenant, email="corrected@example.com")

    async with AsyncSessionLocal() as db:
        await backfill_lead_contact(
            db, lead_id, fetcher=fetcher_returning(graph_response(email="stale@example.com"))
        )
        await db.commit()

    assert (await reload(lead_id)).email == "corrected@example.com"


@pytest.mark.asyncio
async def test_backfilling_a_complete_lead_is_a_no_op(tenant):
    lead_id = await make_lead(tenant, name="Ada Lovelace", email="ada@example.com")

    async def explode(leadgen_id, token):
        raise AssertionError("a complete lead must not call the provider")

    async with AsyncSessionLocal() as db:
        result = await backfill_lead_contact(db, lead_id, fetcher=explode)
        await db.commit()
    assert result["status"] == "skipped"


@pytest.mark.asyncio
async def test_repeated_backfill_is_stable(tenant):
    """Running it twice must not duplicate values or flip the state back."""
    lead_id = await make_lead(tenant)
    payload = graph_response(full_name="Ada", email="ada@example.com", phone_number="+1555")

    async with AsyncSessionLocal() as db:
        await backfill_lead_contact(db, lead_id, fetcher=fetcher_returning(payload))
        await db.commit()
    first = await reload(lead_id)

    async with AsyncSessionLocal() as db:
        second_result = await backfill_lead_contact(db, lead_id, fetcher=fetcher_returning(payload))
        await db.commit()
    second = await reload(lead_id)

    assert second_result["status"] == "skipped"
    assert (first.name, first.email, first.phone) == (second.name, second.email, second.phone)


# --------------------------------------------------------------------------
# Attempt tracking and queueing
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_attempts_are_counted(tenant):
    lead_id = await make_lead(tenant)
    for _ in range(2):
        async with AsyncSessionLocal() as db:
            with pytest.raises(RuntimeError):
                await backfill_lead_contact(db, lead_id, fetcher=fetcher_raising(RuntimeError("x")))
            await db.commit()

    metadata = (await reload(lead_id)).source_metadata
    assert metadata["enrichment_attempts"] == 2
    assert metadata["enrichment_last_attempt_at"]


@pytest.mark.asyncio
async def test_transient_failure_propagates_so_the_job_can_retry(tenant):
    """The job system owns the backoff; swallowing the error would lose the retry."""
    lead_id = await make_lead(tenant)
    async with AsyncSessionLocal() as db:
        with pytest.raises(RuntimeError):
            await backfill_lead_contact(db, lead_id, fetcher=fetcher_raising(RuntimeError("503")))


@pytest.mark.asyncio
async def test_queueing_a_backfill_twice_produces_one_job(tenant):
    lead_id = await make_lead(tenant)
    async with AsyncSessionLocal() as db:
        lead = await db.get(Lead, lead_id)
        first = await enqueue_backfill(db, lead)
        second = await enqueue_backfill(db, lead)
        await db.commit()

    assert first == second
    async with AsyncSessionLocal() as db:
        jobs = list(
            await db.scalars(
                select(BackgroundJob).where(BackgroundJob.job_type == LEAD_BACKFILL)
            )
        )
    assert len([j for j in jobs if j.payload.get("lead_id") == str(lead_id)]) == 1


@pytest.mark.asyncio
async def test_the_worker_handler_runs_a_backfill(tenant, monkeypatch):
    from app.jobs.handlers import handle_lead_backfill
    from app.services import lead_backfill_service

    monkeypatch.setattr(
        lead_backfill_service,
        "fetch_lead_details",
        fetcher_returning(graph_response(full_name="Ada", email="ada@example.com")),
    )
    lead_id = await make_lead(tenant)
    async with AsyncSessionLocal() as db:
        job = BackgroundJob(
            organization_id=tenant["org_id"],
            job_type=LEAD_BACKFILL,
            payload={"lead_id": str(lead_id)},
        )
        db.add(job)
        await db.flush()
        result = await handle_lead_backfill(db, job)
        await db.commit()

    assert result["status"] == COMPLETE
    assert (await reload(lead_id)).email == "ada@example.com"


# --------------------------------------------------------------------------
# Selection and isolation
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_only_incomplete_meta_leads_are_selected(tenant):
    incomplete = await make_lead(tenant)
    await make_lead(tenant, name="Ada Lovelace", email="ada@example.com")

    async with AsyncSessionLocal() as db:
        pending = await leads_awaiting_contact(db, tenant["org_id"])
    assert [lead.id for lead in pending] == [incomplete]


def test_manually_entered_leads_are_never_backfilled():
    """Only Meta leads have a provider to ask; a typed-in lead has nothing behind it."""
    manual = Lead(name="Typed by hand", source="manual", email=None, phone=None)
    assert needs_backfill(manual) is False


@pytest.mark.asyncio
async def test_backfill_refuses_a_lead_from_another_organization(tenant):
    lead_id = await make_lead(tenant)
    async with AsyncSessionLocal() as db:
        with pytest.raises(BackfillUnavailable):
            await backfill_lead_contact(db, lead_id, organization_id=uuid.uuid4())


@pytest.mark.asyncio
async def test_awaiting_contact_does_not_cross_tenants(tenant):
    await make_lead(tenant)
    other = uuid.uuid4().hex[:8]
    async with AsyncSessionLocal() as db:
        org = Organization(name=f"Other {other}", slug=f"other-bf-{other}", demo_mode=False)
        db.add(org)
        await db.flush()
        pending = await leads_awaiting_contact(db, org.id)
    assert pending == []


# --------------------------------------------------------------------------
# HTTP
# --------------------------------------------------------------------------


async def _token(http: AsyncClient, email: str) -> str:
    response = await http.post("/api/v1/auth/login", json={"email": email, "password": PASSWORD})
    return response.json()["access_token"]


@pytest.mark.asyncio
async def test_endpoint_lists_leads_awaiting_contact(tenant):
    lead_id = await make_lead(tenant)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as http:
        token = await _token(http, tenant["email"])
        response = await http.get(
            f"/api/v1/clients/{tenant['client_id']}/leads/awaiting-contact",
            headers={"Authorization": f"Bearer {token}"},
        )
    assert response.status_code == 200, response.text
    assert [item["id"] for item in response.json()] == [str(lead_id)]


@pytest.mark.asyncio
async def test_endpoint_reports_that_retrieval_is_unavailable(tenant):
    """The caller is told it did not work, rather than shown a hopeful 'queued'."""
    async with AsyncSessionLocal() as db:
        integration = await db.scalar(
            select(Integration).where(Integration.organization_id == tenant["org_id"])
        )
        integration.secret_ref = None
        await db.commit()

    lead_id = await make_lead(tenant)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as http:
        token = await _token(http, tenant["email"])
        response = await http.post(
            f"/api/v1/clients/{tenant['client_id']}/leads/{lead_id}/backfill",
            headers={"Authorization": f"Bearer {token}"},
        )
    assert response.status_code == 200
    assert response.json()["status"] == UNAVAILABLE


@pytest.mark.asyncio
async def test_bulk_endpoint_queues_one_job_per_lead(tenant):
    await make_lead(tenant)
    await make_lead(tenant)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as http:
        token = await _token(http, tenant["email"])
        response = await http.post(
            f"/api/v1/clients/{tenant['client_id']}/leads/backfill",
            headers={"Authorization": f"Bearer {token}"},
        )
    assert response.status_code == 200
    assert response.json()["queued"] == 2


@pytest.mark.asyncio
async def test_backfill_endpoint_refuses_another_tenants_lead(tenant):
    lead_id = await make_lead(tenant)
    other = uuid.uuid4().hex[:8]
    async with AsyncSessionLocal() as db:
        org = Organization(name=f"Nosy {other}", slug=f"nosy-{other}", demo_mode=False)
        user = User(
            email=f"nosy-{other}@example.com",
            hashed_password=hash_password(PASSWORD),
            full_name="Nosy",
        )
        db.add_all([org, user])
        await db.flush()
        client = Client(organization_id=org.id, business_name="Nosy Co", industry="saas")
        db.add(client)
        db.add(OrganizationMember(organization_id=org.id, user_id=user.id, role=MemberRole.owner))
        await db.commit()
        nosy_email, nosy_client = user.email, client.id

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as http:
        token = await _token(http, nosy_email)
        response = await http.post(
            f"/api/v1/clients/{nosy_client}/leads/{lead_id}/backfill",
            headers={"Authorization": f"Bearer {token}"},
        )
    assert response.status_code == 404
