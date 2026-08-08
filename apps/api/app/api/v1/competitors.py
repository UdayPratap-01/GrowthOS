from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import AuthContext, get_current_auth
from app.db.session import get_db
from app.schemas.competitor import CompetitorCreate, CompetitorOut, CompetitorUpdate
from app.services.competitor_service import CompetitorService

router = APIRouter(prefix="/clients/{client_id}/competitors", tags=["competitors"])


@router.get("", response_model=list[CompetitorOut])
async def list_competitors(
    client_id: UUID,
    auth: AuthContext = Depends(get_current_auth),
    db: AsyncSession = Depends(get_db),
) -> list[CompetitorOut]:
    return await CompetitorService(db).list(auth.organization_id, client_id)


@router.post("", response_model=CompetitorOut, status_code=201)
async def create_competitor(
    client_id: UUID,
    data: CompetitorCreate,
    auth: AuthContext = Depends(get_current_auth),
    db: AsyncSession = Depends(get_db),
) -> CompetitorOut:
    return await CompetitorService(db).create(auth.organization_id, client_id, data)


@router.patch("/{competitor_id}", response_model=CompetitorOut)
async def update_competitor(
    client_id: UUID,
    competitor_id: UUID,
    data: CompetitorUpdate,
    auth: AuthContext = Depends(get_current_auth),
    db: AsyncSession = Depends(get_db),
) -> CompetitorOut:
    return await CompetitorService(db).update(auth.organization_id, client_id, competitor_id, data)


@router.delete("/{competitor_id}", status_code=204)
async def delete_competitor(
    client_id: UUID,
    competitor_id: UUID,
    auth: AuthContext = Depends(get_current_auth),
    db: AsyncSession = Depends(get_db),
) -> None:
    await CompetitorService(db).delete(auth.organization_id, client_id, competitor_id)
