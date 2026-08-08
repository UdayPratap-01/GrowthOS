from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import AuthContext, get_current_auth
from app.db.session import get_db
from app.schemas.campaigns import CampaignOut
from app.services.campaign_service import CampaignService

router = APIRouter(tags=["campaigns"])


@router.get("/campaigns", response_model=list[CampaignOut])
async def list_campaigns(
    client_id: UUID | None = Query(default=None),
    platform: str | None = Query(default=None),
    auth: AuthContext = Depends(get_current_auth),
    db: AsyncSession = Depends(get_db),
) -> list[CampaignOut]:
    return await CampaignService(db).list_campaigns(
        auth.organization_id, client_id=client_id, platform=platform
    )


@router.get("/clients/{client_id}/campaigns", response_model=list[CampaignOut])
async def list_client_campaigns(
    client_id: UUID,
    platform: str | None = Query(default=None),
    auth: AuthContext = Depends(get_current_auth),
    db: AsyncSession = Depends(get_db),
) -> list[CampaignOut]:
    return await CampaignService(db).list_campaigns(
        auth.organization_id, client_id=client_id, platform=platform
    )
