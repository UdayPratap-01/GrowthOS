from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import AuthContext, get_current_auth
from app.db.session import get_db
from app.schemas.dashboard import DashboardOut
from app.services.dashboard_service import DashboardService

router = APIRouter(prefix="/dashboard", tags=["dashboard"])


@router.get("", response_model=DashboardOut)
async def get_dashboard(
    auth: AuthContext = Depends(get_current_auth),
    db: AsyncSession = Depends(get_db),
) -> DashboardOut:
    return await DashboardService(db).get_dashboard(auth.organization_id, auth.demo_mode)
