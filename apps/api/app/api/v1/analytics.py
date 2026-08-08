from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import AuthContext, get_current_auth
from app.db.session import get_db
from app.schemas.analytics import AnalyticsOut
from app.services.analytics_service import AnalyticsService

router = APIRouter(tags=["analytics"])


@router.get("/analytics", response_model=AnalyticsOut)
async def org_analytics(
    period_days: int = Query(default=30, ge=7, le=90),
    auth: AuthContext = Depends(get_current_auth),
    db: AsyncSession = Depends(get_db),
) -> AnalyticsOut:
    return await AnalyticsService(db).get_analytics(
        auth.organization_id, client_id=None, period_days=period_days, demo_mode=auth.demo_mode
    )


@router.get("/clients/{client_id}/analytics", response_model=AnalyticsOut)
async def client_analytics(
    client_id: UUID,
    period_days: int = Query(default=30, ge=7, le=90),
    auth: AuthContext = Depends(get_current_auth),
    db: AsyncSession = Depends(get_db),
) -> AnalyticsOut:
    return await AnalyticsService(db).get_analytics(
        auth.organization_id, client_id=client_id, period_days=period_days, demo_mode=auth.demo_mode
    )
