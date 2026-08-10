"""
Billing foundation: plans, subscription state and an event log.

No payment provider is integrated and none is implied. What exists here is the
state a SaaS needs before it can take money — which plan an organization is on,
what that plan permits, whether the subscription is in good standing, and an
append-only record of how it got there.

Deliberately absent: any card, token, or provider secret. The provider-specific
identifiers stored are references (a customer id, a subscription id) which are
useless on their own; anything sensitive stays with the provider.
"""

from __future__ import annotations

import enum
from datetime import datetime
from uuid import UUID

from sqlalchemy import JSON, Boolean, DateTime, Enum, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class SubscriptionStatus(str, enum.Enum):
    """
    The lifecycle of a paying relationship.

    `PAST_DUE` exists as a distinct state from `CANCELLED` on purpose: a failed
    payment is usually an expired card, and cutting off a customer's access the
    moment a charge bounces loses accounts that would have paid.
    """

    TRIALING = "TRIALING"
    ACTIVE = "ACTIVE"
    PAST_DUE = "PAST_DUE"
    CANCELLED = "CANCELLED"
    EXPIRED = "EXPIRED"


#: States in which the product is usable. `PAST_DUE` is included: it is a grace
#: period, enforced by `grace_period_ends_at` rather than by immediate lockout.
USABLE_STATUSES = frozenset(
    {SubscriptionStatus.TRIALING, SubscriptionStatus.ACTIVE, SubscriptionStatus.PAST_DUE}
)


class Plan(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """
    A named set of limits and features.

    Limits live in a JSON map keyed by usage metric so a new metered resource
    does not require a migration. A metric absent from the map is unlimited,
    which is the safe default: a plan that forgot to mention a limit should not
    accidentally block the customer.
    """

    __tablename__ = "plans"

    code: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: {metric: allowance}. Absent metric means no limit.
    limits: Mapped[dict] = mapped_column(JSON, default=dict)
    #: Feature flags this plan unlocks, e.g. {"video_generation": true}.
    features: Mapped[dict] = mapped_column(JSON, default=dict)
    seats: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    trial_days: Mapped[int] = mapped_column(Integer, nullable=False, default=14)
    is_public: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class OrganizationSubscription(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """
    One organization's standing with us.

    Separate from the pre-existing `subscriptions` table, which stores a plan
    name as a bare string with no lifecycle. That table is left alone; this one
    is the authority for enforcement.
    """

    __tablename__ = "organization_subscriptions"

    organization_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        unique=True,
        nullable=False,
        index=True,
    )
    plan_code: Mapped[str] = mapped_column(String(64), nullable=False, default="free")
    status: Mapped[SubscriptionStatus] = mapped_column(
        Enum(SubscriptionStatus, name="subscription_status", native_enum=False),
        nullable=False,
        default=SubscriptionStatus.TRIALING,
        index=True,
    )

    trial_ends_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    current_period_start: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    current_period_end: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    #: How long a failed payment is tolerated before access stops.
    grace_period_ends_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    #: Set when the customer cancels but has paid through the period.
    cancel_at_period_end: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    #: Which integration owns this subscription; "none" until one is connected.
    provider: Mapped[str] = mapped_column(String(32), nullable=False, default="none")
    #: Opaque references. Never a card, a token or an API secret.
    provider_customer_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    provider_subscription_id: Mapped[str | None] = mapped_column(String(255), nullable=True)

    #: Per-organization limit overrides that beat the plan, for negotiated deals.
    limit_overrides: Mapped[dict] = mapped_column(JSON, default=dict)


class BillingEvent(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """
    Append-only history of everything that changed a subscription.

    Billing questions are almost always historical — "why was I downgraded",
    "when did the trial end" — and a mutable status column cannot answer them.
    """

    __tablename__ = "billing_events"

    organization_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    #: subscription.created | subscription.status_changed | plan.changed |
    #: trial.started | trial.ended | limit.exceeded | payment.recorded
    event_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    from_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    to_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    plan_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    details: Mapped[dict] = mapped_column(JSON, default=dict)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
