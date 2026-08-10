"""Usage and billing state for the calling organization."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import AuthContext, get_current_auth
from app.db.session import get_db
from app.schemas.usage import UsageRecordOut, UsageSummaryOut
from app.services.usage_service import Metric, UsageService, current_period

router = APIRouter(prefix="/usage", tags=["usage"])


@router.get("", response_model=UsageSummaryOut)
async def usage_summary(
    period: str | None = Query(default=None, pattern=r"^\d{4}-\d{2}$"),
    auth: AuthContext = Depends(get_current_auth),
    db: AsyncSession = Depends(get_db),
) -> UsageSummaryOut:
    """What this organization has consumed. Always scoped to the caller's org."""
    summary = await UsageService(db).summary(auth.organization_id, period=period)
    return UsageSummaryOut(
        organization_id=auth.organization_id,
        period=summary.period,
        totals={metric: summary.get(metric) for metric in Metric.ALL},
    )


@router.get("/{metric}/records", response_model=list[UsageRecordOut])
async def usage_records(
    metric: str,
    auth: AuthContext = Depends(get_current_auth),
    db: AsyncSession = Depends(get_db),
) -> list[UsageRecordOut]:
    """The individual events behind a total, so a charge can be explained."""
    records = await UsageService(db).timeline(auth.organization_id, metric)
    return [UsageRecordOut.model_validate(record) for record in records]


@router.get("/period")
async def billing_period() -> dict:
    return {"period": current_period()}
