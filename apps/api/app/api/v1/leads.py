from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import AuthContext, get_current_auth
from app.db.session import get_db
from app.models.enums import LeadStatus
from app.schemas.lead import LeadCreate, LeadOut, LeadUpdate
from app.services.lead_service import LeadService

router = APIRouter(prefix="/clients/{client_id}/leads", tags=["leads"])


@router.get("", response_model=list[LeadOut])
async def list_leads(
    client_id: UUID,
    status: LeadStatus | None = Query(default=None),
    auth: AuthContext = Depends(get_current_auth),
    db: AsyncSession = Depends(get_db),
) -> list[LeadOut]:
    return await LeadService(db).list_leads(auth.organization_id, client_id, status=status)


@router.post("", response_model=LeadOut, status_code=201)
async def create_lead(
    client_id: UUID,
    data: LeadCreate,
    auth: AuthContext = Depends(get_current_auth),
    db: AsyncSession = Depends(get_db),
) -> LeadOut:
    return await LeadService(db).create_lead(auth.organization, client_id, data)


@router.patch("/{lead_id}", response_model=LeadOut)
async def update_lead(
    client_id: UUID,
    lead_id: UUID,
    data: LeadUpdate,
    auth: AuthContext = Depends(get_current_auth),
    db: AsyncSession = Depends(get_db),
) -> LeadOut:
    return await LeadService(db).update_lead(auth.organization_id, client_id, lead_id, data)


@router.delete("/{lead_id}", status_code=204)
async def delete_lead(
    client_id: UUID,
    lead_id: UUID,
    auth: AuthContext = Depends(get_current_auth),
    db: AsyncSession = Depends(get_db),
) -> None:
    await LeadService(db).delete_lead(auth.organization_id, client_id, lead_id)


@router.post("/{lead_id}/score", response_model=LeadOut)
async def rescore_lead(
    client_id: UUID,
    lead_id: UUID,
    auth: AuthContext = Depends(get_current_auth),
    db: AsyncSession = Depends(get_db),
) -> LeadOut:
    return await LeadService(db).rescore(auth.organization, client_id, lead_id)
