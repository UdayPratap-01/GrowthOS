from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class PlanOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    code: str
    name: str
    description: str | None = None
    limits: dict
    features: dict
    seats: int
    trial_days: int


class SubscriptionOut(BaseModel):
    organization_id: UUID
    plan_code: str
    plan_name: str
    status: str
    trial_ends_at: datetime | None = None
    current_period_end: datetime | None = None
    grace_period_ends_at: datetime | None = None
    cancel_at_period_end: bool = False
    features: dict = Field(default_factory=dict)
    #: "none" until a provider integration exists. Never a key or a secret.
    payment_provider: str = "none"


class QuotaOut(BaseModel):
    metric: str
    #: None means unlimited on this plan.
    limit: float | None = None
    used: float
    remaining: float | None = None
    exceeded: bool


class BillingEventOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    event_type: str
    from_status: str | None = None
    to_status: str | None = None
    plan_code: str | None = None
    reason: str | None = None
    occurred_at: datetime


class PlanChangeRequest(BaseModel):
    plan_code: str
