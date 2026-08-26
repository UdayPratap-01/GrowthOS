"""P1-8 — usage is organization-scoped, idempotent, and free of pricing."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.core.security import hash_password
from app.db.session import AsyncSessionLocal
from app.main import app
from app.models.client import Client
from app.models.enums import MemberRole
from app.models.organization import Organization, OrganizationMember
from app.models.usage import UsageRecord
from app.models.user import User
from app.services.usage_service import (
    Metric,
    PendingUsage,
    UsageService,
    current_period,
    flush_usage,
    meter,
    queue_usage,
    start_usage_buffer,
)

PASSWORD = "Str0ng-Test-Passw0rd!"


@pytest.fixture
async def org():
    suffix = uuid.uuid4().hex[:8]
    async with AsyncSessionLocal() as db:
        organization = Organization(name=f"Usage {suffix}", slug=f"usage-{suffix}", demo_mode=False)
        user = User(
            email=f"usage-{suffix}@example.com",
            hashed_password=hash_password(PASSWORD),
            full_name="Usage",
        )
        db.add_all([organization, user])
        await db.flush()
        db.add(
            OrganizationMember(
                organization_id=organization.id, user_id=user.id, role=MemberRole.owner
            )
        )
        client = Client(
            organization_id=organization.id, business_name="Usage Co", industry="saas"
        )
        db.add(client)
        await db.commit()
        return {"id": organization.id, "email": user.email, "client_id": client.id}


async def record(org_id, metric, quantity=1, key=None, **kwargs):
    async with AsyncSessionLocal() as db:
        result = await UsageService(db).record(
            organization_id=org_id,
            metric=metric,
            quantity=quantity,
            idempotency_key=key or uuid.uuid4().hex,
            **kwargs,
        )
        await db.commit()
        return result


async def total(org_id, metric, **kwargs):
    async with AsyncSessionLocal() as db:
        return await UsageService(db).total(org_id, metric, **kwargs)


# --------------------------------------------------------------------------
# Idempotency — the property that keeps invoices correct
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_same_key_records_once(org):
    key = f"image:{uuid.uuid4()}"
    first = await record(org["id"], Metric.IMAGE_GENERATION, key=key)
    second = await record(org["id"], Metric.IMAGE_GENERATION, key=key)

    assert first is not None
    assert second is None, "a repeat must be a no-op, not a second charge"
    assert await total(org["id"], Metric.IMAGE_GENERATION) == 1


@pytest.mark.asyncio
async def test_a_retried_job_does_not_bill_twice(org):
    """A job that fails after persisting its asset and is retried re-meters it."""
    asset_id = uuid.uuid4()
    for _ in range(3):
        async with AsyncSessionLocal() as db:
            await meter(
                db,
                organization_id=org["id"],
                metric=Metric.VIDEO_GENERATION,
                idempotency_key=f"video:{asset_id}",
            )
            await db.commit()

    assert await total(org["id"], Metric.VIDEO_GENERATION) == 1


@pytest.mark.asyncio
async def test_distinct_events_are_counted_separately(org):
    await record(org["id"], Metric.IMAGE_GENERATION, key=f"image:{uuid.uuid4()}")
    await record(org["id"], Metric.IMAGE_GENERATION, key=f"image:{uuid.uuid4()}")
    assert await total(org["id"], Metric.IMAGE_GENERATION) == 2


@pytest.mark.asyncio
async def test_a_duplicate_does_not_break_the_caller(org):
    """Metering must never be the reason a completed generation reports failure."""
    key = f"lead:{uuid.uuid4()}"
    async with AsyncSessionLocal() as db:
        await meter(db, organization_id=org["id"], metric=Metric.LEAD, idempotency_key=key)
        await meter(db, organization_id=org["id"], metric=Metric.LEAD, idempotency_key=key)
        await db.commit()
    assert await total(org["id"], Metric.LEAD) == 1


@pytest.mark.asyncio
async def test_metering_failure_is_swallowed(org):
    """An unknown metric is a programming error, not a reason to fail a request."""
    async with AsyncSessionLocal() as db:
        await meter(
            db,
            organization_id=org["id"],
            metric="not_a_real_metric",
            idempotency_key=uuid.uuid4().hex,
        )
        await db.commit()


@pytest.mark.asyncio
async def test_recording_without_an_organization_is_ignored():
    orphan_key = f"orphan:{uuid.uuid4()}"
    async with AsyncSessionLocal() as db:
        await meter(db, organization_id=None, metric=Metric.LEAD, idempotency_key=orphan_key)
        await db.commit()

    async with AsyncSessionLocal() as db:
        found = await db.scalar(
            select(UsageRecord).where(UsageRecord.idempotency_key == orphan_key)
        )
    assert found is None, "usage with nobody to attribute it to must not be invented"


# --------------------------------------------------------------------------
# Scoping
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_usage_is_isolated_between_organizations(org):
    other = uuid.uuid4().hex[:8]
    async with AsyncSessionLocal() as db:
        second = Organization(name=f"Other {other}", slug=f"other-usage-{other}")
        db.add(second)
        await db.commit()
        second_id = second.id

    await record(org["id"], Metric.AI_REQUEST, 5)
    await record(second_id, Metric.AI_REQUEST, 2)

    assert await total(org["id"], Metric.AI_REQUEST) == 5
    assert await total(second_id, Metric.AI_REQUEST) == 2


@pytest.mark.asyncio
async def test_client_attribution_is_recorded_but_usage_belongs_to_the_org(org):
    await record(org["id"], Metric.IMAGE_GENERATION, client_id=org["client_id"])
    async with AsyncSessionLocal() as db:
        stored = await db.scalar(
            select(UsageRecord).where(UsageRecord.organization_id == org["id"])
        )
    assert stored.client_id == org["client_id"]
    assert stored.organization_id == org["id"]


# --------------------------------------------------------------------------
# Periods
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_usage_is_bucketed_by_month(org):
    last_month = datetime.now(timezone.utc).replace(day=1) - timedelta(days=1)
    await record(org["id"], Metric.AI_REQUEST, 3, occurred_at=last_month)
    await record(org["id"], Metric.AI_REQUEST, 4)

    assert await total(org["id"], Metric.AI_REQUEST) == 4
    assert await total(org["id"], Metric.AI_REQUEST, period=current_period(last_month)) == 3


@pytest.mark.asyncio
async def test_the_period_is_stored_not_derived_at_read_time(org):
    """A late-arriving record must land in the month it happened."""
    moment = datetime(2026, 1, 15, tzinfo=timezone.utc)
    stored = await record(org["id"], Metric.AI_REQUEST, occurred_at=moment)
    assert stored.period == "2026-01"


@pytest.mark.asyncio
async def test_client_and_storage_counts_are_standing_totals(org):
    """A plan caps how many clients you may have, not how many you added this month."""
    last_month = datetime.now(timezone.utc).replace(day=1) - timedelta(days=1)
    await record(org["id"], Metric.CLIENT, 1, occurred_at=last_month)
    await record(org["id"], Metric.CLIENT, 1)

    summary_total = await total(org["id"], Metric.CLIENT)
    assert summary_total == 2


# --------------------------------------------------------------------------
# Summary
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_summary_aggregates_each_metric(org):
    await record(org["id"], Metric.AI_REQUEST, 2)
    await record(org["id"], Metric.AI_TOKENS, 1500)
    await record(org["id"], Metric.IMAGE_GENERATION, 1)

    async with AsyncSessionLocal() as db:
        summary = await UsageService(db).summary(org["id"])

    assert summary.get(Metric.AI_REQUEST) == 2
    assert summary.get(Metric.AI_TOKENS) == 1500
    assert summary.get(Metric.IMAGE_GENERATION) == 1
    assert summary.get(Metric.VIDEO_GENERATION) == 0


def test_no_pricing_lives_in_the_usage_layer():
    """
    Prices change and vary by contract; consumption is a fact. Mixing them means
    a price change silently rewrites history. Identifiers are checked rather
    than the raw text, so the module may still *discuss* pricing in a comment.
    """
    import ast
    import inspect

    from app.services import usage_service

    tree = ast.parse(inspect.getsource(usage_service))
    identifiers = {
        node.id if isinstance(node, ast.Name) else getattr(node, "attr", "") or getattr(node, "arg", "")
        for node in ast.walk(tree)
        if isinstance(node, (ast.Name, ast.Attribute, ast.arg))
    }
    identifiers |= {
        target.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Assign)
        for target in node.targets
        if isinstance(target, ast.Name)
    }
    banned = ("price", "cost", "usd", "currency", "rate_card", "amount_due")
    offenders = {
        name for name in identifiers if any(term in name.lower() for term in banned)
    }
    assert not offenders, f"pricing does not belong in the usage layer: {offenders}"


@pytest.mark.asyncio
async def test_fractional_quantities_survive_the_round_trip(org):
    await record(org["id"], Metric.STORAGE_BYTES, 1536.5)
    assert await total(org["id"], Metric.STORAGE_BYTES) == pytest.approx(1536.5)


# --------------------------------------------------------------------------
# Buffering
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_buffered_usage_is_written_on_flush(org):
    start_usage_buffer()
    queued = queue_usage(
        PendingUsage(org["id"], Metric.AI_REQUEST, 1, f"buffered:{uuid.uuid4()}")
    )
    assert queued is True
    assert await total(org["id"], Metric.AI_REQUEST) == 0, "not written before the flush"

    written = await flush_usage()
    assert written == 1
    assert await total(org["id"], Metric.AI_REQUEST) == 1


@pytest.mark.asyncio
async def test_queueing_without_a_buffer_reports_that_it_did_not_take(org):
    await flush_usage()  # ensure no buffer is active
    assert (
        queue_usage(PendingUsage(org["id"], Metric.AI_REQUEST, 1, f"no-buffer:{uuid.uuid4()}"))
        is False
    )


@pytest.mark.asyncio
async def test_flushing_an_empty_buffer_is_harmless():
    start_usage_buffer()
    assert await flush_usage() == 0


# --------------------------------------------------------------------------
# Real call sites
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_an_ai_call_is_metered_against_the_request_organization(org):
    from app.ai.providers.base import Message
    from app.ai.providers.factory import get_ai_provider
    from app.observability.logging import bind_request_context, clear_request_context

    bind_request_context(organization_id=str(org["id"]))
    start_usage_buffer()
    try:
        await get_ai_provider().complete([Message(role="user", content="hello")])
        await flush_usage()
    finally:
        clear_request_context()

    assert await total(org["id"], Metric.AI_REQUEST) == 1


@pytest.mark.asyncio
async def test_an_ai_call_with_no_tenant_context_is_not_attributed():
    from app.ai.providers.base import Message
    from app.ai.providers.factory import get_ai_provider
    from app.observability.logging import clear_request_context

    clear_request_context()
    start_usage_buffer()
    await get_ai_provider().complete([Message(role="user", content="hello")])
    assert await flush_usage() == 0


@pytest.mark.asyncio
async def test_creating_a_client_is_metered(org):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as http:
        login = await http.post(
            "/api/v1/auth/login", json={"email": org["email"], "password": PASSWORD}
        )
        token = login.json()["access_token"]
        created = await http.post(
            "/api/v1/clients",
            headers={"Authorization": f"Bearer {token}"},
            json={"business_name": "Metered Co", "industry": "saas"},
        )
    assert created.status_code in {200, 201}, created.text

    async with AsyncSessionLocal() as db:
        stored = await db.scalar(
            select(UsageRecord).where(
                UsageRecord.organization_id == org["id"], UsageRecord.metric == Metric.CLIENT
            )
        )
    assert stored is not None


# --------------------------------------------------------------------------
# HTTP
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_usage_endpoint_reports_the_callers_own_usage(org):
    await record(org["id"], Metric.AI_REQUEST, 7)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as http:
        login = await http.post(
            "/api/v1/auth/login", json={"email": org["email"], "password": PASSWORD}
        )
        response = await http.get(
            "/api/v1/usage",
            headers={"Authorization": f"Bearer {login.json()['access_token']}"},
        )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["organization_id"] == str(org["id"])
    assert body["totals"][Metric.AI_REQUEST] == 7
    assert Metric.VIDEO_GENERATION in body["totals"], "unused metrics report zero, not absent"


@pytest.mark.asyncio
async def test_usage_endpoint_never_shows_another_organization(org):
    other = uuid.uuid4().hex[:8]
    async with AsyncSessionLocal() as db:
        second = Organization(name=f"Rival {other}", slug=f"rival-{other}")
        db.add(second)
        await db.commit()
        await record(second.id, Metric.AI_REQUEST, 99)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as http:
        login = await http.post(
            "/api/v1/auth/login", json={"email": org["email"], "password": PASSWORD}
        )
        response = await http.get(
            "/api/v1/usage",
            headers={"Authorization": f"Bearer {login.json()['access_token']}"},
        )

    assert response.json()["totals"][Metric.AI_REQUEST] == 0


@pytest.mark.asyncio
async def test_usage_endpoint_requires_authentication():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as http:
        response = await http.get("/api/v1/usage")
    assert response.status_code in {401, 403}


@pytest.mark.asyncio
async def test_records_endpoint_explains_a_total(org):
    await record(
        org["id"],
        Metric.IMAGE_GENERATION,
        key=f"image:{uuid.uuid4()}",
        details={"provider": "openai"},
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as http:
        login = await http.post(
            "/api/v1/auth/login", json={"email": org["email"], "password": PASSWORD}
        )
        response = await http.get(
            f"/api/v1/usage/{Metric.IMAGE_GENERATION}/records",
            headers={"Authorization": f"Bearer {login.json()['access_token']}"},
        )

    assert response.status_code == 200
    assert response.json()[0]["details"]["provider"] == "openai"
