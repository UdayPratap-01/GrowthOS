"""
P1-9 — the billing foundation.

Two things are being proven here. First, that enforcement is real: a plan limit
actually blocks the request, and it does so by reading the same usage records
P1-8 writes rather than a parallel counter. Second, that nothing pretends a
payment happened — there is no provider, and the code says so out loud rather
than returning a plausible-looking success.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.core.security import hash_password
from app.db.session import AsyncSessionLocal
from app.main import app
from app.models.billing import (
    USABLE_STATUSES,
    BillingEvent,
    OrganizationSubscription,
    SubscriptionStatus,
)
from app.models.enums import MemberRole
from app.models.organization import Organization, OrganizationMember
from app.models.user import User
from app.services.billing_service import (
    UNLIMITED,
    BillingService,
    FeatureUnavailable,
    QuotaExceeded,
    SubscriptionInactive,
    UnconfiguredPaymentProvider,
    get_payment_provider,
)
from app.services.usage_service import Metric, UsageService

PASSWORD = "Str0ng-Test-Passw0rd!"


@pytest.fixture
async def org():
    suffix = uuid.uuid4().hex[:8]
    async with AsyncSessionLocal() as db:
        organization = Organization(
            name=f"Billing {suffix}", slug=f"billing-{suffix}", demo_mode=False
        )
        user = User(
            email=f"billing-{suffix}@example.com",
            hashed_password=hash_password(PASSWORD),
            full_name="Billing Owner",
        )
        db.add_all([organization, user])
        await db.flush()
        db.add(
            OrganizationMember(
                organization_id=organization.id, user_id=user.id, role=MemberRole.owner
            )
        )
        await db.commit()
        return {"id": organization.id, "email": user.email}


@pytest.fixture
async def member_org():
    """An organization whose caller is a MEMBER, for the role checks."""
    suffix = uuid.uuid4().hex[:8]
    async with AsyncSessionLocal() as db:
        organization = Organization(
            name=f"Member {suffix}", slug=f"member-{suffix}", demo_mode=False
        )
        user = User(
            email=f"member-{suffix}@example.com",
            hashed_password=hash_password(PASSWORD),
            full_name="Member",
        )
        db.add_all([organization, user])
        await db.flush()
        db.add(
            OrganizationMember(
                organization_id=organization.id, user_id=user.id, role=MemberRole.member
            )
        )
        await db.commit()
        return {"id": organization.id, "email": user.email}


async def token_for(email: str) -> str:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test/api/v1") as http:
        resp = await http.post("/auth/login", json={"email": email, "password": PASSWORD})
        return resp.json()["access_token"]


async def use(org_id, metric: str, quantity: float = 1) -> None:
    async with AsyncSessionLocal() as db:
        await UsageService(db).record(
            organization_id=org_id,
            metric=metric,
            quantity=quantity,
            idempotency_key=f"{metric}:{uuid.uuid4()}",
        )
        await db.commit()


async def put_on_plan(org_id, plan_code: str) -> None:
    async with AsyncSessionLocal() as db:
        await BillingService(db).change_plan(org_id, plan_code)
        await db.commit()


# --------------------------------------------------------------------------
# Plans
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_default_catalogue_installs_once(org):
    async with AsyncSessionLocal() as db:
        service = BillingService(db)
        first = await service.ensure_plans()
        await db.commit()
    async with AsyncSessionLocal() as db:
        second = await BillingService(db).ensure_plans()
        await db.commit()

    assert {plan.code for plan in first} == {plan.code for plan in second}
    assert len(first) == len(second), "seeding twice must not duplicate the catalogue"


@pytest.mark.asyncio
async def test_plans_carry_limits_and_features(org):
    async with AsyncSessionLocal() as db:
        plan = await BillingService(db).get_plan("free")
        await db.commit()

    assert plan is not None
    assert plan.limits[Metric.IMAGE_GENERATION] == 10
    assert plan.features["video_generation"] is False


@pytest.mark.asyncio
async def test_no_price_is_stored_on_a_plan(org):
    """Pricing belongs to the payment provider, not to the enforcement layer."""
    async with AsyncSessionLocal() as db:
        plan = await BillingService(db).get_plan("growth")
        await db.commit()

    columns = {column.name for column in plan.__table__.columns}
    for banned in ("price", "amount", "currency", "cost", "monthly_price"):
        assert banned not in columns


# --------------------------------------------------------------------------
# Subscription lifecycle
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_an_organization_without_a_subscription_gets_a_trial(org):
    async with AsyncSessionLocal() as db:
        subscription = await BillingService(db).get_subscription(org["id"])
        await db.commit()

    assert subscription.status == SubscriptionStatus.TRIALING
    assert subscription.trial_ends_at is not None


@pytest.mark.asyncio
async def test_reading_a_subscription_twice_does_not_create_two(org):
    async with AsyncSessionLocal() as db:
        await BillingService(db).get_subscription(org["id"])
        await db.commit()
    async with AsyncSessionLocal() as db:
        await BillingService(db).get_subscription(org["id"])
        await db.commit()

    async with AsyncSessionLocal() as db:
        rows = await db.scalars(
            select(OrganizationSubscription).where(
                OrganizationSubscription.organization_id == org["id"]
            )
        )
        assert len(list(rows)) == 1


@pytest.mark.asyncio
async def test_every_status_transition_is_recorded(org):
    async with AsyncSessionLocal() as db:
        service = BillingService(db)
        await service.get_subscription(org["id"])
        await service.set_status(org["id"], SubscriptionStatus.ACTIVE, reason="Payment received.")
        await service.set_status(org["id"], SubscriptionStatus.PAST_DUE, reason="Charge declined.")
        await db.commit()

    async with AsyncSessionLocal() as db:
        history = await BillingService(db).history(org["id"])
        await db.commit()

    transitions = [
        (event.from_status, event.to_status)
        for event in history
        if event.event_type == "subscription.status_changed"
    ]
    assert ("ACTIVE", "PAST_DUE") in transitions
    assert ("TRIALING", "ACTIVE") in transitions


@pytest.mark.asyncio
async def test_a_repeated_status_change_is_not_logged_twice(org):
    async with AsyncSessionLocal() as db:
        service = BillingService(db)
        await service.set_status(org["id"], SubscriptionStatus.ACTIVE)
        await service.set_status(org["id"], SubscriptionStatus.ACTIVE)
        await db.commit()

    async with AsyncSessionLocal() as db:
        events = await db.scalars(
            select(BillingEvent).where(
                BillingEvent.organization_id == org["id"],
                BillingEvent.event_type == "subscription.status_changed",
            )
        )
        assert len(list(events)) == 1


@pytest.mark.asyncio
async def test_a_lapsed_trial_expires_when_read(org):
    async with AsyncSessionLocal() as db:
        subscription = await BillingService(db).get_subscription(org["id"])
        subscription.trial_ends_at = datetime.now(timezone.utc) - timedelta(days=1)
        await db.commit()

    async with AsyncSessionLocal() as db:
        refreshed = await BillingService(db).expire_if_due(org["id"])
        await db.commit()

    assert refreshed.status == SubscriptionStatus.EXPIRED


@pytest.mark.asyncio
async def test_a_trial_still_running_is_left_alone(org):
    async with AsyncSessionLocal() as db:
        subscription = await BillingService(db).get_subscription(org["id"])
        subscription.trial_ends_at = datetime.now(timezone.utc) + timedelta(days=3)
        await db.commit()

    async with AsyncSessionLocal() as db:
        refreshed = await BillingService(db).expire_if_due(org["id"])
        await db.commit()

    assert refreshed.status == SubscriptionStatus.TRIALING


@pytest.mark.asyncio
async def test_a_failed_payment_does_not_lock_the_customer_out_immediately(org):
    """PAST_DUE is a grace period. An expired card should not stop the work."""
    async with AsyncSessionLocal() as db:
        service = BillingService(db)
        subscription = await service.set_status(org["id"], SubscriptionStatus.PAST_DUE)
        await db.commit()

    assert subscription.grace_period_ends_at is not None
    assert SubscriptionStatus.PAST_DUE in USABLE_STATUSES

    async with AsyncSessionLocal() as db:
        # No exception: still inside the grace window.
        await BillingService(db).require_quota(org["id"], Metric.AI_REQUEST)
        await db.commit()


@pytest.mark.asyncio
async def test_an_exhausted_grace_period_expires(org):
    async with AsyncSessionLocal() as db:
        service = BillingService(db)
        subscription = await service.set_status(org["id"], SubscriptionStatus.PAST_DUE)
        subscription.grace_period_ends_at = datetime.now(timezone.utc) - timedelta(hours=1)
        await db.commit()

    async with AsyncSessionLocal() as db:
        refreshed = await BillingService(db).expire_if_due(org["id"])
        await db.commit()

    assert refreshed.status == SubscriptionStatus.EXPIRED


@pytest.mark.asyncio
async def test_paying_clears_the_grace_deadline(org):
    async with AsyncSessionLocal() as db:
        service = BillingService(db)
        await service.set_status(org["id"], SubscriptionStatus.PAST_DUE)
        subscription = await service.set_status(org["id"], SubscriptionStatus.ACTIVE)
        await db.commit()

    assert subscription.grace_period_ends_at is None


@pytest.mark.asyncio
async def test_an_expired_subscription_blocks_work(org):
    async with AsyncSessionLocal() as db:
        await BillingService(db).set_status(org["id"], SubscriptionStatus.EXPIRED)
        await db.commit()

    async with AsyncSessionLocal() as db:
        with pytest.raises(SubscriptionInactive):
            await BillingService(db).require_quota(org["id"], Metric.AI_REQUEST)
        await db.rollback()


@pytest.mark.asyncio
async def test_a_cancelled_subscription_blocks_work(org):
    async with AsyncSessionLocal() as db:
        subscription = await BillingService(db).set_status(
            org["id"], SubscriptionStatus.CANCELLED
        )
        await db.commit()

    assert subscription.cancelled_at is not None

    async with AsyncSessionLocal() as db:
        with pytest.raises(SubscriptionInactive):
            await BillingService(db).require_feature(org["id"], "video_generation")
        await db.rollback()


@pytest.mark.asyncio
async def test_changing_plan_is_recorded_with_the_previous_plan(org):
    await put_on_plan(org["id"], "growth")

    async with AsyncSessionLocal() as db:
        history = await BillingService(db).history(org["id"])
        await db.commit()

    change = next(event for event in history if event.event_type == "plan.changed")
    assert change.plan_code == "growth"
    assert change.details["from_plan"] == "starter"


@pytest.mark.asyncio
async def test_an_unknown_plan_is_refused(org):
    async with AsyncSessionLocal() as db:
        with pytest.raises(ValueError):
            await BillingService(db).change_plan(org["id"], "enterprise-unlimited")
        await db.rollback()


# --------------------------------------------------------------------------
# Quota enforcement, reading the P1-8 meter
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_quota_counts_real_recorded_usage(org):
    await put_on_plan(org["id"], "free")
    for _ in range(4):
        await use(org["id"], Metric.IMAGE_GENERATION)

    async with AsyncSessionLocal() as db:
        check = await BillingService(db).check_quota(org["id"], Metric.IMAGE_GENERATION)
        await db.commit()

    assert check.used == 4
    assert check.limit == 10
    assert check.remaining == 6
    assert check.allowed


@pytest.mark.asyncio
async def test_exceeding_the_plan_limit_raises(org):
    await put_on_plan(org["id"], "free")
    for _ in range(10):
        await use(org["id"], Metric.IMAGE_GENERATION)

    async with AsyncSessionLocal() as db:
        with pytest.raises(QuotaExceeded) as raised:
            await BillingService(db).require_quota(org["id"], Metric.IMAGE_GENERATION)
        await db.rollback()

    assert raised.value.metric == Metric.IMAGE_GENERATION
    assert raised.value.limit == 10


@pytest.mark.asyncio
async def test_the_limit_is_a_ceiling_not_a_suggestion(org):
    """The request that would cross the line is the one refused."""
    await put_on_plan(org["id"], "free")
    for _ in range(9):
        await use(org["id"], Metric.IMAGE_GENERATION)

    async with AsyncSessionLocal() as db:
        service = BillingService(db)
        await service.require_quota(org["id"], Metric.IMAGE_GENERATION)  # the tenth
        with pytest.raises(QuotaExceeded):
            await service.require_quota(org["id"], Metric.IMAGE_GENERATION, amount=2)
        await db.rollback()


@pytest.mark.asyncio
async def test_a_metric_absent_from_the_plan_is_unlimited(org):
    """A plan that forgot a metric must not accidentally block the customer."""
    await put_on_plan(org["id"], "agency")
    for _ in range(50):
        await use(org["id"], Metric.AI_REQUEST)

    async with AsyncSessionLocal() as db:
        check = await BillingService(db).check_quota(org["id"], Metric.AI_REQUEST)
        await BillingService(db).require_quota(org["id"], Metric.AI_REQUEST)
        await db.commit()

    assert check.limit == UNLIMITED
    assert check.remaining == float("inf")


@pytest.mark.asyncio
async def test_a_zero_limit_blocks_the_first_attempt(org):
    """Free has no video allowance at all; nothing may slip through."""
    await put_on_plan(org["id"], "free")
    async with AsyncSessionLocal() as db:
        with pytest.raises(QuotaExceeded):
            await BillingService(db).require_quota(org["id"], Metric.VIDEO_GENERATION)
        await db.rollback()


@pytest.mark.asyncio
async def test_an_upgrade_lifts_the_ceiling(org):
    await put_on_plan(org["id"], "free")
    for _ in range(10):
        await use(org["id"], Metric.IMAGE_GENERATION)

    await put_on_plan(org["id"], "growth")

    async with AsyncSessionLocal() as db:
        await BillingService(db).require_quota(org["id"], Metric.IMAGE_GENERATION)
        await db.commit()


@pytest.mark.asyncio
async def test_a_negotiated_override_beats_the_plan(org):
    await put_on_plan(org["id"], "free")
    async with AsyncSessionLocal() as db:
        subscription = await BillingService(db).get_subscription(org["id"])
        subscription.limit_overrides = {Metric.IMAGE_GENERATION: 500}
        await db.commit()

    for _ in range(11):
        await use(org["id"], Metric.IMAGE_GENERATION)

    async with AsyncSessionLocal() as db:
        await BillingService(db).require_quota(org["id"], Metric.IMAGE_GENERATION)
        await db.commit()


@pytest.mark.asyncio
async def test_usage_from_another_organization_does_not_count(org, member_org):
    await put_on_plan(org["id"], "free")
    for _ in range(20):
        await use(member_org["id"], Metric.IMAGE_GENERATION)

    async with AsyncSessionLocal() as db:
        await BillingService(db).require_quota(org["id"], Metric.IMAGE_GENERATION)
        await db.commit()


@pytest.mark.asyncio
async def test_a_refusal_is_recorded_as_a_billing_event(org):
    await put_on_plan(org["id"], "free")
    async with AsyncSessionLocal() as db:
        with pytest.raises(QuotaExceeded):
            await BillingService(db).require_quota(org["id"], Metric.VIDEO_GENERATION)
        # The service records before raising; the caller decides to keep it.
        await db.commit()

    async with AsyncSessionLocal() as db:
        events = await db.scalars(
            select(BillingEvent).where(
                BillingEvent.organization_id == org["id"],
                BillingEvent.event_type == "limit.exceeded",
            )
        )
        assert list(events), "an upgrade prompt needs evidence behind it"


@pytest.mark.asyncio
async def test_storage_bytes_are_enforced_as_a_standing_total(org):
    """Storage is stock, not flow: last month's files still occupy the disk."""
    await put_on_plan(org["id"], "free")
    await use(org["id"], Metric.STORAGE_BYTES, quantity=400 * 1024 * 1024)

    async with AsyncSessionLocal() as db:
        check = await BillingService(db).check_quota(org["id"], Metric.STORAGE_BYTES)
        await db.commit()

    assert check.used == 400 * 1024 * 1024
    assert check.allowed


# --------------------------------------------------------------------------
# Feature access
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_feature_off_the_plan_is_refused(org):
    await put_on_plan(org["id"], "free")
    async with AsyncSessionLocal() as db:
        with pytest.raises(FeatureUnavailable) as raised:
            await BillingService(db).require_feature(org["id"], "autopilot")
        await db.rollback()

    assert raised.value.feature == "autopilot"


@pytest.mark.asyncio
async def test_a_feature_on_the_plan_is_allowed(org):
    await put_on_plan(org["id"], "growth")
    async with AsyncSessionLocal() as db:
        await BillingService(db).require_feature(org["id"], "autopilot")
        await db.commit()


@pytest.mark.asyncio
async def test_an_unknown_feature_defaults_to_denied(org):
    """Unknown means not paid for. Defaulting open gives the product away."""
    await put_on_plan(org["id"], "growth")
    async with AsyncSessionLocal() as db:
        with pytest.raises(FeatureUnavailable):
            await BillingService(db).require_feature(org["id"], "white_label")
        await db.rollback()


# --------------------------------------------------------------------------
# No payments are taken, and none are faked
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_payment_provider_refuses_rather_than_pretending():
    provider = get_payment_provider()
    assert isinstance(provider, UnconfiguredPaymentProvider)

    with pytest.raises(NotImplementedError):
        await provider.create_customer(organization_id=uuid.uuid4(), email="a@example.com")
    with pytest.raises(NotImplementedError):
        await provider.start_subscription(customer_id="cus_x", plan_code="growth")
    with pytest.raises(NotImplementedError):
        await provider.cancel_subscription(subscription_id="sub_x", at_period_end=True)


@pytest.mark.asyncio
async def test_a_new_subscription_claims_no_payment_provider(org):
    async with AsyncSessionLocal() as db:
        subscription = await BillingService(db).get_subscription(org["id"])
        await db.commit()

    assert subscription.provider == "none"
    assert subscription.provider_customer_id is None
    assert subscription.provider_subscription_id is None


@pytest.mark.asyncio
async def test_no_provider_secret_is_stored_on_a_subscription(org):
    async with AsyncSessionLocal() as db:
        subscription = await BillingService(db).get_subscription(org["id"])
        await db.commit()

    columns = {column.name for column in subscription.__table__.columns}
    for banned in ("api_key", "secret", "token", "card_number", "payment_method_token"):
        assert banned not in columns


# --------------------------------------------------------------------------
# HTTP surface
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_plan_catalogue_is_visible(org):
    token = await token_for(org["email"])
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test/api/v1") as http:
        resp = await http.get("/billing/plans", headers={"Authorization": f"Bearer {token}"})

    assert resp.status_code == 200
    codes = {plan["code"] for plan in resp.json()}
    assert {"free", "starter", "growth"} <= codes


@pytest.mark.asyncio
async def test_the_subscription_endpoint_reports_state_and_no_secrets(org):
    token = await token_for(org["email"])
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test/api/v1") as http:
        resp = await http.get("/billing/subscription", headers={"Authorization": f"Bearer {token}"})

    body = resp.json()
    assert resp.status_code == 200
    assert body["status"] in {status.value for status in SubscriptionStatus}
    assert body["payment_provider"] == "none"
    serialized = resp.text.lower()
    for banned in ("secret", "api_key", "sk_live", "card"):
        assert banned not in serialized


@pytest.mark.asyncio
async def test_quotas_show_limit_and_consumption_together(org):
    await put_on_plan(org["id"], "free")
    await use(org["id"], Metric.IMAGE_GENERATION, quantity=3)
    token = await token_for(org["email"])

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test/api/v1") as http:
        resp = await http.get("/billing/quotas", headers={"Authorization": f"Bearer {token}"})

    quotas = {row["metric"]: row for row in resp.json()}
    assert quotas[Metric.IMAGE_GENERATION]["limit"] == 10
    assert quotas[Metric.IMAGE_GENERATION]["used"] == 3
    assert quotas[Metric.IMAGE_GENERATION]["remaining"] == 7
    assert quotas[Metric.IMAGE_GENERATION]["exceeded"] is False
    # Unlimited is expressed as null rather than a large number.
    assert quotas[Metric.INTEGRATION_SYNC]["limit"] is None


@pytest.mark.asyncio
async def test_only_a_billing_manager_can_change_the_plan(member_org):
    token = await token_for(member_org["email"])
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test/api/v1") as http:
        resp = await http.post(
            "/billing/plan",
            json={"plan_code": "growth"},
            headers={"Authorization": f"Bearer {token}"},
        )

    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "PERMISSION_DENIED"


@pytest.mark.asyncio
async def test_an_owner_can_change_the_plan(org):
    token = await token_for(org["email"])
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test/api/v1") as http:
        resp = await http.post(
            "/billing/plan",
            json={"plan_code": "growth"},
            headers={"Authorization": f"Bearer {token}"},
        )

    assert resp.status_code == 200
    assert resp.json()["plan_code"] == "growth"


@pytest.mark.asyncio
async def test_billing_history_is_visible_to_the_organization(org):
    await put_on_plan(org["id"], "growth")
    token = await token_for(org["email"])
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test/api/v1") as http:
        resp = await http.get("/billing/events", headers={"Authorization": f"Bearer {token}"})

    types = {event["event_type"] for event in resp.json()}
    assert "plan.changed" in types


@pytest.mark.asyncio
async def test_billing_state_is_not_visible_without_authentication():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test/api/v1") as http:
        resp = await http.get("/billing/subscription")
    assert resp.status_code == 401


# --------------------------------------------------------------------------
# Enforcement at the API boundary
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_an_over_quota_request_is_refused_with_402(org):
    """The refusal happens before the expensive work, not after."""
    await put_on_plan(org["id"], "free")
    for _ in range(10):
        await use(org["id"], Metric.IMAGE_GENERATION)

    async with AsyncSessionLocal() as db:
        organization = await db.get(Organization, org["id"])
        from app.models.client import Client

        client = Client(
            organization_id=organization.id, business_name="Quota Co", industry="saas"
        )
        db.add(client)
        await db.commit()
        client_id = client.id

    token = await token_for(org["email"])
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test/api/v1") as http:
        resp = await http.post(
            "/creative/images/generate",
            json={"client_id": str(client_id), "prompt": "A product shot", "quantity": 1},
            headers={"Authorization": f"Bearer {token}"},
        )

    assert resp.status_code == 402
    assert resp.json()["error"]["code"] == "QUOTA_EXCEEDED"


@pytest.mark.asyncio
async def test_a_feature_off_the_plan_is_refused_at_the_endpoint(org):
    await put_on_plan(org["id"], "free")
    async with AsyncSessionLocal() as db:
        from app.models.client import Client

        client = Client(organization_id=org["id"], business_name="Video Co", industry="saas")
        db.add(client)
        await db.commit()
        client_id = client.id

    token = await token_for(org["email"])
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test/api/v1") as http:
        resp = await http.post(
            "/creative/videos/generate",
            json={"client_id": str(client_id), "prompt": "A 10s clip"},
            headers={"Authorization": f"Bearer {token}"},
        )

    assert resp.status_code == 402
    assert resp.json()["error"]["code"] == "FEATURE_NOT_IN_PLAN"


@pytest.mark.asyncio
async def test_a_request_within_quota_is_not_blocked_by_billing(org):
    """Enforcement must not become a new way for working features to fail."""
    await put_on_plan(org["id"], "growth")
    token = await token_for(org["email"])

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test/api/v1") as http:
        resp = await http.post(
            "/clients",
            json={"business_name": "In Budget Co", "industry": "saas"},
            headers={"Authorization": f"Bearer {token}"},
        )

    assert resp.status_code == 201


@pytest.mark.asyncio
async def test_the_client_limit_is_enforced_on_creation(org):
    await put_on_plan(org["id"], "free")
    token = await token_for(org["email"])

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test/api/v1") as http:
        headers = {"Authorization": f"Bearer {token}"}
        first = await http.post(
            "/clients", json={"business_name": "One", "industry": "saas"}, headers=headers
        )
        second = await http.post(
            "/clients", json={"business_name": "Two", "industry": "saas"}, headers=headers
        )

    assert first.status_code == 201
    assert second.status_code == 402, "the free plan allows a single client"
