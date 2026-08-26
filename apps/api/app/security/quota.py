"""
Plan enforcement as a FastAPI dependency.

Sits alongside the rate limiter from P1-1 and answers a different question. Rate
limiting asks "is this too fast"; a quota asks "is this included in what you
pay for". Both return before the expensive work starts.

402 is used for a quota, not 403: the caller is authenticated and permitted,
the plan simply does not cover it, and the fix is a payment rather than a
permission change.
"""

from __future__ import annotations

from fastapi import Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import AuthContext, get_current_auth
from app.db.session import get_db
from app.services.billing_service import (
    BillingService,
    FeatureUnavailable,
    QuotaExceeded,
    SubscriptionInactive,
)


def requires_quota(metric: str):
    async def _dependency(
        auth: AuthContext = Depends(get_current_auth),
        db: AsyncSession = Depends(get_db),
    ) -> None:
        try:
            await BillingService(db).require_quota(auth.organization_id, metric)
        except QuotaExceeded as exc:
            # The refusal is itself a billing event worth keeping — it is the
            # evidence behind an upgrade prompt. Raising would roll the request
            # back and take the event with it, so commit first.
            await db.commit()
            raise HTTPException(
                status_code=status.HTTP_402_PAYMENT_REQUIRED,
                detail=f"QUOTA_EXCEEDED: {exc}",
            ) from exc
        except SubscriptionInactive as exc:
            raise HTTPException(
                status_code=status.HTTP_402_PAYMENT_REQUIRED,
                detail=f"SUBSCRIPTION_INACTIVE: {exc}",
            ) from exc

    return _dependency


def requires_feature(feature: str):
    async def _dependency(
        auth: AuthContext = Depends(get_current_auth),
        db: AsyncSession = Depends(get_db),
    ) -> None:
        try:
            await BillingService(db).require_feature(auth.organization_id, feature)
        except FeatureUnavailable as exc:
            raise HTTPException(
                status_code=status.HTTP_402_PAYMENT_REQUIRED,
                detail=f"FEATURE_NOT_IN_PLAN: {exc}",
            ) from exc
        except SubscriptionInactive as exc:
            raise HTTPException(
                status_code=status.HTTP_402_PAYMENT_REQUIRED,
                detail=f"SUBSCRIPTION_INACTIVE: {exc}",
            ) from exc

    return _dependency
