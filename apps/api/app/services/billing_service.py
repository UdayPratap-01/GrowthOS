"""
Billing and plan enforcement.

Scope, stated plainly: this decides what an organization is *allowed* to do. It
does not take money. There is no payment provider integration, no card handling,
and no code path that marks anything as paid — `PaymentProvider` is an interface
with one implementation that refuses, so a future integration has a seam to slot
into and the current system cannot pretend a payment happened.

Enforcement reads plan limits and compares them against the meter from P1-8, so
"you have used your 50 videos" is a fact derived from recorded events rather
than a second counter that can drift from the first.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Protocol
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.billing import (
    USABLE_STATUSES,
    BillingEvent,
    OrganizationSubscription,
    Plan,
    SubscriptionStatus,
)
from app.services.usage_service import Metric, UsageService

logger = logging.getLogger("growthos.billing")

UNLIMITED = -1

#: Seeded on first use so a fresh installation has something coherent to enforce.
#: Limits are per calendar month except clients and storage, which are standing
#: totals. A metric absent from `limits` is unlimited.
DEFAULT_PLANS: tuple[dict, ...] = (
    {
        "code": "free",
        "name": "Free",
        "description": "Evaluation. Enough to see the product work on real data.",
        "seats": 2,
        "trial_days": 0,
        "sort_order": 0,
        "limits": {
            Metric.CLIENT: 1,
            Metric.AI_REQUEST: 100,
            Metric.IMAGE_GENERATION: 10,
            Metric.VIDEO_GENERATION: 0,
            Metric.REPORT_GENERATION: 3,
            Metric.STORAGE_BYTES: 500 * 1024 * 1024,
        },
        "features": {"video_generation": False, "autopilot": False, "api_access": False},
    },
    {
        "code": "starter",
        "name": "Starter",
        "seats": 5,
        "trial_days": 14,
        "sort_order": 1,
        "limits": {
            Metric.CLIENT: 5,
            Metric.AI_REQUEST: 2_000,
            Metric.IMAGE_GENERATION: 200,
            Metric.VIDEO_GENERATION: 20,
            Metric.REPORT_GENERATION: 50,
            Metric.STORAGE_BYTES: 20 * 1024 * 1024 * 1024,
        },
        "features": {"video_generation": True, "autopilot": False, "api_access": True},
    },
    {
        "code": "growth",
        "name": "Growth",
        "seats": 15,
        "trial_days": 14,
        "sort_order": 2,
        "limits": {
            Metric.CLIENT: 25,
            Metric.AI_REQUEST: 20_000,
            Metric.IMAGE_GENERATION: 2_000,
            Metric.VIDEO_GENERATION: 200,
            Metric.REPORT_GENERATION: 500,
            Metric.STORAGE_BYTES: 200 * 1024 * 1024 * 1024,
        },
        "features": {"video_generation": True, "autopilot": True, "api_access": True},
    },
    {
        "code": "agency",
        "name": "Agency",
        "seats": 50,
        "trial_days": 14,
        "sort_order": 3,
        "limits": {Metric.CLIENT: 200},
        "features": {"video_generation": True, "autopilot": True, "api_access": True},
    },
)


class QuotaExceeded(Exception):
    """The organization's plan does not allow this. A 402, not a 500."""

    def __init__(self, metric: str, limit: float, used: float, plan_code: str) -> None:
        super().__init__(
            f"Your {plan_code} plan allows {limit:g} {metric.replace('_', ' ')} "
            f"per period; {used:g} have been used."
        )
        self.metric = metric
        self.limit = limit
        self.used = used
        self.plan_code = plan_code


class FeatureUnavailable(Exception):
    def __init__(self, feature: str, plan_code: str) -> None:
        super().__init__(f"{feature.replace('_', ' ').title()} is not included in the {plan_code} plan.")
        self.feature = feature
        self.plan_code = plan_code


class SubscriptionInactive(Exception):
    def __init__(self, status: SubscriptionStatus) -> None:
        super().__init__(f"This organization's subscription is {status.value}.")
        self.status = status


@dataclass
class QuotaCheck:
    metric: str
    limit: float
    used: float
    allowed: bool

    @property
    def remaining(self) -> float:
        if self.limit == UNLIMITED:
            return float("inf")
        return max(self.limit - self.used, 0)


# --------------------------------------------------------------------------
# Payment provider seam
# --------------------------------------------------------------------------


class PaymentProvider(Protocol):
    """
    The seam a real provider will implement.

    Kept deliberately small: create a customer, start a subscription, cancel one.
    Everything else — invoices, dunning, proration — belongs to the provider and
    reaches us as webhooks, which is why `BillingEvent` exists.
    """

    name: str

    async def create_customer(self, *, organization_id: UUID, email: str) -> str: ...

    async def start_subscription(self, *, customer_id: str, plan_code: str) -> str: ...

    async def cancel_subscription(self, *, subscription_id: str, at_period_end: bool) -> None: ...


class UnconfiguredPaymentProvider:
    """
    The only implementation, and it refuses.

    A stub that returned a fake customer id would let the system report a
    successful subscription that no provider knows about — exactly the class of
    false success this codebase is being hardened against.
    """

    name = "none"

    async def create_customer(self, *, organization_id: UUID, email: str) -> str:
        raise NotImplementedError(
            "No payment provider is configured. Set up a provider integration before "
            "attempting to charge; this system will not fake a customer record."
        )

    async def start_subscription(self, *, customer_id: str, plan_code: str) -> str:
        raise NotImplementedError("No payment provider is configured.")

    async def cancel_subscription(self, *, subscription_id: str, at_period_end: bool) -> None:
        raise NotImplementedError("No payment provider is configured.")


def get_payment_provider() -> PaymentProvider:
    return UnconfiguredPaymentProvider()


# --------------------------------------------------------------------------
# Service
# --------------------------------------------------------------------------


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


class BillingService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    # -- plans ------------------------------------------------------------

    async def ensure_plans(self) -> list[Plan]:
        """Idempotently install the default catalogue."""
        existing = {plan.code: plan for plan in await self.db.scalars(select(Plan))}
        for spec in DEFAULT_PLANS:
            if spec["code"] in existing:
                continue
            plan = Plan(
                code=spec["code"],
                name=spec["name"],
                description=spec.get("description"),
                limits=dict(spec["limits"]),
                features=dict(spec["features"]),
                seats=spec["seats"],
                trial_days=spec["trial_days"],
                sort_order=spec["sort_order"],
            )
            self.db.add(plan)
            existing[plan.code] = plan
        await self.db.flush()
        return sorted(existing.values(), key=lambda plan: plan.sort_order)

    async def get_plan(self, code: str) -> Plan | None:
        await self.ensure_plans()
        return await self.db.scalar(select(Plan).where(Plan.code == code))

    # -- subscription -----------------------------------------------------

    async def get_subscription(self, organization_id: UUID) -> OrganizationSubscription:
        """Every organization has one; created on first read if absent."""
        subscription = await self.db.scalar(
            select(OrganizationSubscription).where(
                OrganizationSubscription.organization_id == organization_id
            )
        )
        if subscription is None:
            subscription = await self.start_trial(organization_id, plan_code="starter")
        return subscription

    async def start_trial(
        self, organization_id: UUID, *, plan_code: str = "starter"
    ) -> OrganizationSubscription:
        plan = await self.get_plan(plan_code)
        trial_days = plan.trial_days if plan else 14
        now = _now()
        subscription = OrganizationSubscription(
            organization_id=organization_id,
            plan_code=plan_code,
            status=SubscriptionStatus.TRIALING if trial_days else SubscriptionStatus.ACTIVE,
            trial_ends_at=now + timedelta(days=trial_days) if trial_days else None,
            current_period_start=now,
            current_period_end=now + timedelta(days=trial_days or 30),
        )
        self.db.add(subscription)
        await self.db.flush()
        await self.record_event(
            organization_id,
            event_type="subscription.created",
            to_status=subscription.status.value,
            plan_code=plan_code,
            reason=f"Trial started for {trial_days} days." if trial_days else "Subscription created.",
        )
        return subscription

    async def set_status(
        self,
        organization_id: UUID,
        status: SubscriptionStatus,
        *,
        reason: str | None = None,
    ) -> OrganizationSubscription:
        subscription = await self.get_subscription(organization_id)
        previous = subscription.status
        if previous == status:
            return subscription

        subscription.status = status
        if status == SubscriptionStatus.PAST_DUE:
            # A failed charge is usually an expired card. Locking the customer
            # out immediately loses accounts that would have paid.
            subscription.grace_period_ends_at = _now() + timedelta(days=7)
        if status == SubscriptionStatus.CANCELLED:
            subscription.cancelled_at = _now()
        if status == SubscriptionStatus.ACTIVE:
            subscription.grace_period_ends_at = None
        await self.db.flush()

        await self.record_event(
            organization_id,
            event_type="subscription.status_changed",
            from_status=previous.value,
            to_status=status.value,
            plan_code=subscription.plan_code,
            reason=reason,
        )
        logger.info(
            "Subscription status changed",
            extra={
                "event": "billing.status_changed",
                "org": str(organization_id),
                "from_status": previous.value,
                "to_status": status.value,
            },
        )
        return subscription

    async def change_plan(
        self, organization_id: UUID, plan_code: str, *, reason: str | None = None
    ) -> OrganizationSubscription:
        plan = await self.get_plan(plan_code)
        if plan is None:
            raise ValueError(f"Unknown plan {plan_code!r}")
        subscription = await self.get_subscription(organization_id)
        previous = subscription.plan_code
        subscription.plan_code = plan_code
        await self.db.flush()
        await self.record_event(
            organization_id,
            event_type="plan.changed",
            plan_code=plan_code,
            reason=reason or f"Plan changed from {previous} to {plan_code}.",
            details={"from_plan": previous},
        )
        return subscription

    async def expire_if_due(self, organization_id: UUID) -> OrganizationSubscription:
        """
        Move a lapsed subscription into its terminal state.

        Time passing is not an event anything listens for, so the transition is
        evaluated when the subscription is read.
        """
        subscription = await self.get_subscription(organization_id)
        now = _now()

        trial_end = _aware(subscription.trial_ends_at)
        if (
            subscription.status == SubscriptionStatus.TRIALING
            and trial_end is not None
            and trial_end <= now
        ):
            return await self.set_status(
                organization_id, SubscriptionStatus.EXPIRED, reason="Trial period ended."
            )

        grace_end = _aware(subscription.grace_period_ends_at)
        if (
            subscription.status == SubscriptionStatus.PAST_DUE
            and grace_end is not None
            and grace_end <= now
        ):
            return await self.set_status(
                organization_id,
                SubscriptionStatus.EXPIRED,
                reason="Grace period after failed payment ended.",
            )

        return subscription

    # -- enforcement ------------------------------------------------------

    async def limits_for(self, organization_id: UUID) -> dict[str, float]:
        subscription = await self.get_subscription(organization_id)
        plan = await self.get_plan(subscription.plan_code)
        limits = dict((plan.limits if plan else {}) or {})
        # A negotiated override beats the catalogue.
        limits.update(subscription.limit_overrides or {})
        return {metric: float(value) for metric, value in limits.items()}

    async def check_quota(self, organization_id: UUID, metric: str) -> QuotaCheck:
        limits = await self.limits_for(organization_id)
        if metric not in limits:
            # Absent means unlimited: a plan that forgot to mention a metric
            # should not accidentally block the customer.
            return QuotaCheck(metric=metric, limit=UNLIMITED, used=0, allowed=True)

        limit = limits[metric]
        used = await UsageService(self.db).total(organization_id, metric)
        return QuotaCheck(metric=metric, limit=limit, used=used, allowed=used < limit)

    async def require_quota(self, organization_id: UUID, metric: str, *, amount: float = 1) -> None:
        """Raise unless the organization may consume `amount` more of `metric`."""
        subscription = await self.expire_if_due(organization_id)
        if subscription.status not in USABLE_STATUSES:
            raise SubscriptionInactive(subscription.status)

        check = await self.check_quota(organization_id, metric)
        if check.limit == UNLIMITED:
            return
        if check.used + amount > check.limit:
            await self.record_event(
                organization_id,
                event_type="limit.exceeded",
                plan_code=subscription.plan_code,
                reason=f"{metric} limit of {check.limit:g} reached.",
                details={"metric": metric, "limit": check.limit, "used": check.used},
            )
            raise QuotaExceeded(metric, check.limit, check.used, subscription.plan_code)

    async def require_feature(self, organization_id: UUID, feature: str) -> None:
        subscription = await self.expire_if_due(organization_id)
        if subscription.status not in USABLE_STATUSES:
            raise SubscriptionInactive(subscription.status)
        plan = await self.get_plan(subscription.plan_code)
        features = (plan.features if plan else {}) or {}
        if not features.get(feature, False):
            raise FeatureUnavailable(feature, subscription.plan_code)

    # -- history ----------------------------------------------------------

    async def record_event(
        self,
        organization_id: UUID,
        *,
        event_type: str,
        from_status: str | None = None,
        to_status: str | None = None,
        plan_code: str | None = None,
        reason: str | None = None,
        details: dict | None = None,
    ) -> BillingEvent:
        event = BillingEvent(
            organization_id=organization_id,
            event_type=event_type,
            from_status=from_status,
            to_status=to_status,
            plan_code=plan_code,
            reason=reason,
            details=details or {},
            occurred_at=_now(),
        )
        self.db.add(event)
        await self.db.flush()
        return event

    async def history(self, organization_id: UUID, *, limit: int = 100) -> list[BillingEvent]:
        rows = await self.db.scalars(
            select(BillingEvent)
            .where(BillingEvent.organization_id == organization_id)
            .order_by(BillingEvent.occurred_at.desc())
            .limit(limit)
        )
        return list(rows)
