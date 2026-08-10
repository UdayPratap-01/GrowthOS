"""
Cross-tenant attack suite.

Every test here plays the same attacker: a fully legitimate, authenticated
member of Organization A who knows the id of a record inside Organization B and
asks for it directly. Nothing is guessed and no token is forged — the only
question is whether the server checks ownership.

The rule being enforced is "not found or forbidden, never the record", and
never a response that distinguishes "someone else owns this" from "this does
not exist", because that difference is itself a disclosure.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.core.security import hash_password
from app.db.session import AsyncSessionLocal
from app.main import app
from app.models.ai_ops import Integration, Report
from app.models.automation import BackgroundJob, CreativeAsset
from app.models.client import Client
from app.models.enums import JobStatus, LeadStatus, MemberRole
from app.models.leads import Lead
from app.models.organization import Organization, OrganizationMember
from app.models.strategy import Strategy
from app.models.user import User

PASSWORD = "Str0ng-Tenancy-Passw0rd!"

#: Acceptable answers when one tenant reaches for another's record. 404 is
#: preferred (it leaks nothing); 403 is acceptable where authorization runs
#: before lookup. 200 never is.
DENIED = {403, 404}


@dataclass
class Tenant:
    email: str
    organization_id: uuid.UUID
    user_id: uuid.UUID
    client_id: uuid.UUID
    lead_id: uuid.UUID
    strategy_id: uuid.UUID
    report_id: uuid.UUID
    asset_id: uuid.UUID
    job_id: uuid.UUID
    integration_id: uuid.UUID


async def _seed_tenant(label: str, role: MemberRole = MemberRole.owner) -> Tenant:
    """One organization with one of everything a neighbour might try to read."""
    suffix = uuid.uuid4().hex[:8]
    email = f"{label}-{suffix}@tenancytest.com"
    async with AsyncSessionLocal() as db:
        org = Organization(name=f"Tenant {label} {suffix}", slug=f"tenant-{label}-{suffix}", demo_mode=False)
        user = User(email=email, hashed_password=hash_password(PASSWORD), full_name=f"{label} user")
        db.add_all([org, user])
        await db.flush()
        db.add(OrganizationMember(organization_id=org.id, user_id=user.id, role=role))

        client = Client(organization_id=org.id, business_name=f"{label} Client", industry="saas")
        db.add(client)
        await db.flush()

        lead = Lead(
            organization_id=org.id,
            client_id=client.id,
            name=f"{label} Prospect",
            email=f"prospect-{suffix}@example.com",
            source="manual",
            status=LeadStatus.new,
        )
        strategy = Strategy(
            organization_id=org.id,
            client_id=client.id,
            title=f"{label} Strategy",
            current_situation="x",
            what_is_happening="y",
            strategy_summary="z",
        )
        report = Report(
            organization_id=org.id,
            client_id=client.id,
            title=f"{label} Report",
            period_start=date.today() - timedelta(days=7),
            period_end=date.today(),
            content={"secret": label},
            status="complete",
        )
        asset = CreativeAsset(
            organization_id=org.id,
            client_id=client.id,
            name=f"{label} Asset",
            asset_type="image",
            status="completed",
            storage_key=f"organizations/{org.id}/creative/{suffix}.png",
            mime_type="image/png",
        )
        job = BackgroundJob(
            organization_id=org.id,
            job_type="report.generate",
            status=JobStatus.queued,
            payload={"client_id": str(client.id)},
            max_attempts=3,
        )
        integration = Integration(
            organization_id=org.id,
            client_id=client.id,
            provider="google_analytics",
            status="connected",
            config={"external_account_id": f"ga-{suffix}"},
        )
        db.add_all([lead, strategy, report, asset, job, integration])
        await db.commit()

        return Tenant(
            email=email,
            organization_id=org.id,
            user_id=user.id,
            client_id=client.id,
            lead_id=lead.id,
            strategy_id=strategy.id,
            report_id=report.id,
            asset_id=asset.id,
            job_id=job.id,
            integration_id=integration.id,
        )


async def _login(http: AsyncClient, email: str) -> dict[str, str]:
    resp = await http.post("/api/v1/auth/login", json={"email": email, "password": PASSWORD})
    assert resp.status_code == 200, resp.text
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


def _client() -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


@pytest.fixture
async def tenants() -> tuple[Tenant, Tenant]:
    return await _seed_tenant("a"), await _seed_tenant("b")


# --------------------------------------------------------------------------
# Direct record access
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_org_a_cannot_read_org_b_client(tenants):
    a, b = tenants
    async with _client() as http:
        headers = await _login(http, a.email)
        resp = await http.get(f"/api/v1/clients/{b.client_id}", headers=headers)
    assert resp.status_code in DENIED, resp.text


@pytest.mark.asyncio
async def test_org_a_cannot_read_org_b_client_context(tenants):
    a, b = tenants
    async with _client() as http:
        headers = await _login(http, a.email)
        resp = await http.get(f"/api/v1/clients/{b.client_id}/context", headers=headers)
    assert resp.status_code in DENIED, resp.text


@pytest.mark.asyncio
async def test_org_a_cannot_list_org_b_leads(tenants):
    a, b = tenants
    async with _client() as http:
        headers = await _login(http, a.email)
        mine = await http.get(f"/api/v1/clients/{a.client_id}/leads", headers=headers)
        foreign = await http.get(f"/api/v1/clients/{b.client_id}/leads", headers=headers)

    assert mine.status_code == 200
    assert str(b.lead_id) not in mine.text
    if foreign.status_code == 200:
        assert foreign.json() == [], "a foreign client must yield nothing, not its leads"
    else:
        assert foreign.status_code in DENIED


@pytest.mark.asyncio
async def test_org_a_cannot_modify_org_b_lead(tenants):
    a, b = tenants
    async with _client() as http:
        headers = await _login(http, a.email)
        via_own_client = await http.patch(
            f"/api/v1/clients/{a.client_id}/leads/{b.lead_id}",
            headers=headers,
            json={"name": "Stolen"},
        )
        via_their_client = await http.patch(
            f"/api/v1/clients/{b.client_id}/leads/{b.lead_id}",
            headers=headers,
            json={"name": "Stolen"},
        )
    assert via_own_client.status_code in DENIED, via_own_client.text
    assert via_their_client.status_code in DENIED, via_their_client.text

    async with AsyncSessionLocal() as db:
        lead = await db.get(Lead, b.lead_id)
    assert lead.name != "Stolen", "the record must be untouched"


@pytest.mark.asyncio
async def test_org_a_cannot_delete_org_b_lead(tenants):
    a, b = tenants
    async with _client() as http:
        headers = await _login(http, a.email)
        resp = await http.delete(
            f"/api/v1/clients/{b.client_id}/leads/{b.lead_id}", headers=headers
        )
    assert resp.status_code in DENIED, resp.text

    async with AsyncSessionLocal() as db:
        assert await db.get(Lead, b.lead_id) is not None


@pytest.mark.asyncio
async def test_org_a_cannot_read_org_b_strategies(tenants):
    a, b = tenants
    async with _client() as http:
        headers = await _login(http, a.email)
        resp = await http.get(f"/api/v1/clients/{b.client_id}/strategies", headers=headers)

    if resp.status_code == 200:
        assert resp.json() == [], "a foreign client must yield nothing, not its strategies"
    else:
        assert resp.status_code in DENIED


@pytest.mark.asyncio
async def test_org_a_cannot_read_org_b_report(tenants):
    a, b = tenants
    async with _client() as http:
        headers = await _login(http, a.email)
        detail = await http.get(
            f"/api/v1/clients/{b.client_id}/reports/{b.report_id}", headers=headers
        )
        pdf = await http.get(
            f"/api/v1/clients/{b.client_id}/reports/{b.report_id}/pdf", headers=headers
        )
    assert detail.status_code in DENIED, detail.text
    assert pdf.status_code in DENIED, pdf.text


@pytest.mark.asyncio
async def test_org_a_cannot_read_org_b_creative_asset(tenants):
    a, b = tenants
    async with _client() as http:
        headers = await _login(http, a.email)
        media = await http.get(f"/api/v1/creative/media/{b.asset_id}", headers=headers)
        listing = await http.get("/api/v1/creative/assets", headers=headers)

    assert media.status_code in DENIED, media.text
    assert str(b.asset_id) not in {row["id"] for row in listing.json()}


@pytest.mark.asyncio
async def test_org_a_cannot_see_org_b_integrations(tenants):
    a, b = tenants
    async with _client() as http:
        headers = await _login(http, a.email)
        resp = await http.get("/api/v1/integrations", headers=headers)

    assert resp.status_code == 200
    blob = resp.text
    assert str(b.integration_id) not in blob
    assert str(b.organization_id) not in blob


@pytest.mark.asyncio
async def test_usage_and_billing_are_reported_per_organization(tenants):
    a, b = tenants
    async with _client() as http:
        headers = await _login(http, a.email)
        usage = await http.get("/api/v1/usage", headers=headers)
        subscription = await http.get("/api/v1/billing/subscription", headers=headers)

    assert usage.status_code == 200
    assert subscription.status_code == 200
    for response in (usage, subscription):
        assert str(b.organization_id) not in response.text


# --------------------------------------------------------------------------
# Jobs — the CRITICAL finding
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_org_a_cannot_read_org_b_job(tenants):
    a, b = tenants
    async with _client() as http:
        headers = await _login(http, a.email)
        resp = await http.get(f"/api/v1/jobs/{b.job_id}", headers=headers)
    assert resp.status_code == 404, resp.text


@pytest.mark.asyncio
async def test_org_a_cannot_cancel_org_b_job(tenants):
    a, b = tenants
    async with _client() as http:
        headers = await _login(http, a.email)
        resp = await http.post(f"/api/v1/jobs/{b.job_id}/cancel", headers=headers)
    assert resp.status_code in DENIED, resp.text

    async with AsyncSessionLocal() as db:
        job = await db.get(BackgroundJob, b.job_id)
    assert job.status == JobStatus.queued, "a foreign job must not be cancellable"


@pytest.mark.asyncio
async def test_org_a_cannot_retry_org_b_job(tenants):
    a, b = tenants
    async with AsyncSessionLocal() as db:
        job = await db.get(BackgroundJob, b.job_id)
        job.status = JobStatus.failed
        job.error = "boom"
        await db.commit()

    async with _client() as http:
        headers = await _login(http, a.email)
        resp = await http.post(f"/api/v1/jobs/{b.job_id}/retry", headers=headers)
    assert resp.status_code in DENIED, resp.text

    async with AsyncSessionLocal() as db:
        job = await db.get(BackgroundJob, b.job_id)
    assert job.status == JobStatus.failed, "a foreign job must not be requeued"


@pytest.mark.asyncio
async def test_processing_jobs_never_touches_another_tenants_queue(tenants):
    """
    The reported vulnerability: `/autopilot/jobs/process` drained the global
    queue, so any authenticated user could execute another organization's work
    using that organization's integrations.
    """
    a, b = tenants
    async with _client() as http:
        headers = await _login(http, a.email)
        resp = await http.post("/api/v1/autopilot/jobs/process", headers=headers)

    assert resp.status_code == 200, resp.text
    assert str(b.job_id) not in resp.json().get("ids", [])

    async with AsyncSessionLocal() as db:
        victim = await db.get(BackgroundJob, b.job_id)
    assert victim.status == JobStatus.queued, "B's job must still be waiting"
    assert victim.attempts == 0, "B's job must not have been claimed by A's request"
    assert victim.locked_by is None


@pytest.mark.asyncio
async def test_with_a_worker_deployed_the_endpoint_only_enqueues(tenants, monkeypatch):
    """
    Production runs a worker, so the request path stops at "queued". Executing
    jobs inside a user request is a development convenience, not the design.
    """
    from app.api.v1 import autopilot
    from app.core.config import Settings

    a, b = tenants
    monkeypatch.setattr(
        autopilot, "app_settings", lambda: Settings(environment="staging", inline_job_execution=False)
    )

    async with _client() as http:
        headers = await _login(http, a.email)
        resp = await http.post("/api/v1/autopilot/jobs/process", headers=headers)

    body = resp.json()
    assert resp.status_code == 200, resp.text
    assert body["processed"] == 0
    assert body["job_id"], "the caller still gets something to poll"
    assert body["poll_url"].endswith(body["job_id"])

    async with AsyncSessionLocal() as db:
        queued = await db.get(BackgroundJob, uuid.UUID(body["job_id"]))
        victim = await db.get(BackgroundJob, b.job_id)
    assert queued.organization_id == a.organization_id
    assert victim.status == JobStatus.queued


@pytest.mark.asyncio
async def test_scoped_processing_runs_only_the_callers_jobs(tenants):
    """The positive half: A's own due work is still executed for A."""
    from app.jobs.handlers import process_organization_jobs

    a, b = tenants
    async with AsyncSessionLocal() as db:
        processed = await process_organization_jobs(db, a.organization_id)
        await db.commit()

    touched = {job.organization_id for job in processed}
    assert touched <= {a.organization_id}, f"processed another tenant's jobs: {touched}"
    assert b.organization_id not in touched

    async with AsyncSessionLocal() as db:
        victim = await db.get(BackgroundJob, b.job_id)
    assert victim.status == JobStatus.queued


@pytest.mark.asyncio
async def test_queue_claim_refuses_a_foreign_job(tenants):
    """Ownership is enforced inside the claim UPDATE, not only above it."""
    from app.jobs.queue import JobQueue

    a, b = tenants
    async with AsyncSessionLocal() as db:
        queue = JobQueue(db, worker_id="attacker")
        stolen = await queue.claim(b.job_id, organization_id=a.organization_id)
        await db.commit()
    assert stolen is None

    async with AsyncSessionLocal() as db:
        queue = JobQueue(db, worker_id="rightful")
        legitimate = await queue.claim(b.job_id, organization_id=b.organization_id)
        await db.commit()
    assert legitimate is not None, "the owner must still be able to claim it"


@pytest.mark.asyncio
async def test_worker_still_drains_every_tenant(tenants):
    """
    Scoping must not break the worker: it is trusted infrastructure and has to
    process all tenants, or B's jobs would never run at all.
    """
    from app.jobs.queue import JobQueue

    a, b = tenants
    async with AsyncSessionLocal() as db:
        # Limit high enough that the assertion is about scoping, not batch size.
        ids = await JobQueue(db)._candidate_ids(datetime.now(timezone.utc), limit=100_000)
    assert b.job_id in ids
    assert a.job_id in ids


@pytest.mark.asyncio
async def test_job_handler_refuses_a_payload_from_another_tenant(tenants):
    """Defense in depth: a job carrying a foreign record id must not run."""
    from app.jobs.handlers import UnrecoverableJobError, handle_generate_image
    from app.models.automation import ImageJob

    a, b = tenants
    async with AsyncSessionLocal() as db:
        image_job = ImageJob(
            organization_id=b.organization_id,
            client_id=b.client_id,
            prompt="victim prompt",
            status=JobStatus.queued,
        )
        db.add(image_job)
        await db.flush()
        forged = BackgroundJob(
            organization_id=a.organization_id,
            job_type="media.generate_image",
            status=JobStatus.running,
            payload={"image_job_id": str(image_job.id)},
            max_attempts=1,
        )
        db.add(forged)
        await db.commit()
        image_job_id = image_job.id

    async with AsyncSessionLocal() as db:
        job = await db.get(BackgroundJob, forged.id)
        with pytest.raises(UnrecoverableJobError):
            await handle_generate_image(db, job)

    async with AsyncSessionLocal() as db:
        untouched = await db.get(ImageJob, image_job_id)
    assert untouched.status == JobStatus.queued


# --------------------------------------------------------------------------
# Disclosure
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_foreign_id_is_indistinguishable_from_a_missing_one(tenants):
    """
    A different answer for "exists but belongs to someone else" would let an
    attacker enumerate another tenant's records without ever reading one.
    """
    a, b = tenants
    ghost = uuid.uuid4()
    async with _client() as http:
        headers = await _login(http, a.email)
        foreign = await http.get(f"/api/v1/jobs/{b.job_id}", headers=headers)
        missing = await http.get(f"/api/v1/jobs/{ghost}", headers=headers)

    assert foreign.status_code == missing.status_code == 404
    assert foreign.json()["error"]["code"] == missing.json()["error"]["code"]


@pytest.mark.asyncio
async def test_the_client_cannot_choose_its_own_organization(tenants):
    """
    Tenant identity comes from the session, so sending someone else's
    organization_id in the body must change nothing.
    """
    a, b = tenants
    async with _client() as http:
        headers = await _login(http, a.email)
        resp = await http.post(
            "/api/v1/clients",
            headers=headers,
            json={
                "business_name": "Injected",
                "industry": "saas",
                "organization_id": str(b.organization_id),
            },
        )

    assert resp.status_code in {200, 201}, resp.text
    created_id = resp.json()["id"]

    async with AsyncSessionLocal() as db:
        created = await db.get(Client, uuid.UUID(created_id))
    assert created.organization_id == a.organization_id, "org must come from the token, not the body"


@pytest.mark.asyncio
async def test_every_seeded_record_stays_with_its_owner(tenants):
    """A blunt sweep: nothing from B may be reachable in any of A's listings."""
    a, b = tenants
    listings = [
        "/api/v1/clients",
        f"/api/v1/clients/{a.client_id}/leads",
        f"/api/v1/clients/{a.client_id}/reports",
        "/api/v1/creative/assets",
        "/api/v1/jobs",
        "/api/v1/integrations",
    ]
    foreign_ids = {
        str(b.client_id),
        str(b.lead_id),
        str(b.report_id),
        str(b.asset_id),
        str(b.job_id),
        str(b.integration_id),
        str(b.organization_id),
    }

    async with _client() as http:
        headers = await _login(http, a.email)
        for path in listings:
            resp = await http.get(path, headers=headers)
            assert resp.status_code == 200, f"{path}: {resp.text}"
            leaked = {value for value in foreign_ids if value in resp.text}
            assert not leaked, f"{path} leaked {leaked}"


@pytest.mark.asyncio
async def test_org_b_still_sees_its_own_records(tenants):
    """The isolation must not be achieved by hiding data from its owner."""
    _, b = tenants
    async with _client() as http:
        headers = await _login(http, b.email)
        client = await http.get(f"/api/v1/clients/{b.client_id}", headers=headers)
        job = await http.get(f"/api/v1/jobs/{b.job_id}", headers=headers)
        report = await http.get(
            f"/api/v1/clients/{b.client_id}/reports/{b.report_id}", headers=headers
        )

    assert client.status_code == 200, client.text
    assert job.status_code == 200, job.text
    assert report.status_code == 200, report.text


@pytest.mark.asyncio
async def test_sessions_of_one_tenant_are_invisible_to_another(tenants):
    a, b = tenants
    async with _client() as http:
        headers = await _login(http, a.email)
        resp = await http.get("/api/v1/auth/sessions", headers=headers)
    assert resp.status_code == 200
    assert str(b.user_id) not in resp.text


@pytest.mark.asyncio
async def test_background_jobs_carry_an_owner(tenants):
    """
    Scoping only works if jobs are attributed. A job created without an
    organization would be invisible to the scoped query and drain-only by the
    worker — acceptable, but it must not happen by accident on tenant work.
    """
    a, _ = tenants
    async with AsyncSessionLocal() as db:
        rows = list(
            await db.scalars(
                select(BackgroundJob).where(BackgroundJob.organization_id == a.organization_id)
            )
        )
    assert rows, "the fixture must have produced at least one owned job"
    assert all(row.organization_id is not None for row in rows)
