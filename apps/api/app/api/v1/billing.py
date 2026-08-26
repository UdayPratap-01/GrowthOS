"""Plan, subscription state and quota visibility for the calling organization."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import AuthContext, get_current_auth
from app.core.permissions import Permission, require_permission
from app.db.session import get_db
from app.schemas.billing import (
    BillingEventOut,
    PlanChangeRequest,
    PlanOut,
    QuotaOut,
    SubscriptionOut,
)
from app.services.billing_service import UNLIMITED, BillingService
from app.services.usage_service import Metric, UsageService

router = APIRouter(prefix="/billing", tags=["billing"])


@router.get("/plans", response_model=list[PlanOut])
async def list_plans(
    _: AuthContext = Depends(get_current_auth),
    db: AsyncSession = Depends(get_db),
) -> list[PlanOut]:
    plans = await BillingService(db).ensure_plans()
    return [PlanOut.model_validate(plan) for plan in plans if plan.is_public]


@router.get("/subscription", response_model=SubscriptionOut)
async def subscription(
    auth: AuthContext = Depends(get_current_auth),
    db: AsyncSession = Depends(get_db),
) -> SubscriptionOut:
    service = BillingService(db)
    # Read-time evaluation: a trial ending is not an event anything listens for.
    record = await service.expire_if_due(auth.organization_id)
    plan = await service.get_plan(record.plan_code)
    return SubscriptionOut(
        organization_id=record.organization_id,
        plan_code=record.plan_code,
        plan_name=plan.name if plan else record.plan_code,
        status=record.status.value,
        trial_ends_at=record.trial_ends_at,
        current_period_end=record.current_period_end,
        grace_period_ends_at=record.grace_period_ends_at,
        cancel_at_period_end=record.cancel_at_period_end,
        features=(plan.features if plan else {}) or {},
        payment_provider=record.provider,
    )


@router.get("/quotas", response_model=list[QuotaOut])
async def quotas(
    auth: AuthContext = Depends(get_current_auth),
    db: AsyncSession = Depends(get_db),
) -> list[QuotaOut]:
    """Limit and consumption side by side, so a UI can warn before a 402."""
    service = BillingService(db)
    limits = await service.limits_for(auth.organization_id)
    usage = UsageService(db)

    out: list[QuotaOut] = []
    for metric in Metric.ALL:
        limit = limits.get(metric, UNLIMITED)
        used = await usage.total(auth.organization_id, metric)
        out.append(
            QuotaOut(
                metric=metric,
                limit=None if limit == UNLIMITED else limit,
                used=used,
                remaining=None if limit == UNLIMITED else max(limit - used, 0),
                exceeded=limit != UNLIMITED and used >= limit,
            )
        )
    return out


@router.get("/events", response_model=list[BillingEventOut])
async def events(
    auth: AuthContext = Depends(get_current_auth),
    db: AsyncSession = Depends(get_db),
) -> list[BillingEventOut]:
    history = await BillingService(db).history(auth.organization_id)
    return [BillingEventOut.model_validate(event) for event in history]


@router.post("/plan", response_model=SubscriptionOut)
async def change_plan(
    data: PlanChangeRequest,
    auth: AuthContext = Depends(require_permission(Permission.billing_manage)),
    db: AsyncSession = Depends(get_db),
) -> SubscriptionOut:
    """
    Move the organization onto another plan.

    No money changes hands here and none is claimed to: with no payment provider
    configured, this is the administrative half only. Wiring a provider means
    implementing `PaymentProvider` and calling it before this runs.
    """
    await BillingService(db).change_plan(
        auth.organization_id, data.plan_code, reason="Requested by an organization administrator."
    )
    return await subscription(auth=auth, db=db)
