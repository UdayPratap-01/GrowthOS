from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import AuthContext, get_current_auth
from app.core.permissions import Permission, require_permission
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
    auth: AuthContext = Depends(require_permission(Permission.lead_write)),
    db: AsyncSession = Depends(get_db),
) -> LeadOut:
    return await LeadService(db).create_lead(auth.organization, client_id, data)


@router.patch("/{lead_id}", response_model=LeadOut)
async def update_lead(
    client_id: UUID,
    lead_id: UUID,
    data: LeadUpdate,
    auth: AuthContext = Depends(require_permission(Permission.lead_write)),
    db: AsyncSession = Depends(get_db),
) -> LeadOut:
    return await LeadService(db).update_lead(auth.organization_id, client_id, lead_id, data)


@router.delete("/{lead_id}", status_code=204)
async def delete_lead(
    client_id: UUID,
    lead_id: UUID,
    auth: AuthContext = Depends(require_permission(Permission.lead_write)),
    db: AsyncSession = Depends(get_db),
) -> None:
    await LeadService(db).delete_lead(auth.organization_id, client_id, lead_id)


@router.get("/awaiting-contact", response_model=list[LeadOut])
async def list_leads_awaiting_contact(
    client_id: UUID,
    auth: AuthContext = Depends(get_current_auth),
    db: AsyncSession = Depends(get_db),
) -> list[LeadOut]:
    """Meta leads we hold identifiers for but no way to contact."""
    from app.services.lead_backfill_service import leads_awaiting_contact

    leads = await leads_awaiting_contact(db, auth.organization_id, client_id=client_id)
    return [LeadOut.model_validate(lead) for lead in leads]


@router.post("/{lead_id}/backfill")
async def backfill_lead(
    client_id: UUID,
    lead_id: UUID,
    auth: AuthContext = Depends(require_permission(Permission.lead_write)),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """
    Retry contact-detail retrieval for one lead.

    Runs the attempt directly so the caller learns the outcome, including that
    it did not work. Reporting "queued" for something that cannot succeed —
    because no token is stored — would be the failure this endpoint exists to
    make visible.
    """
    from app.services.lead_backfill_service import BackfillUnavailable, backfill_lead_contact

    try:
        return await backfill_lead_contact(db, lead_id, organization_id=auth.organization_id)
    except BackfillUnavailable as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail=f"LEAD_BACKFILL_FAILED: The Meta API did not return this lead's details ({type(exc).__name__}).",
        ) from exc


@router.post("/backfill")
async def backfill_pending_leads(
    client_id: UUID,
    auth: AuthContext = Depends(require_permission(Permission.lead_write)),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Queue a retrieval attempt for every lead still missing contact details."""
    from app.services.lead_backfill_service import enqueue_backfill, leads_awaiting_contact

    leads = await leads_awaiting_contact(db, auth.organization_id, client_id=client_id)
    job_ids = [await enqueue_backfill(db, lead) for lead in leads]
    return {"queued": len(job_ids), "job_ids": job_ids}


@router.post("/{lead_id}/score", response_model=LeadOut)
async def rescore_lead(
    client_id: UUID,
    lead_id: UUID,
    auth: AuthContext = Depends(require_permission(Permission.lead_write)),
    db: AsyncSession = Depends(get_db),
) -> LeadOut:
    return await LeadService(db).rescore(auth.organization, client_id, lead_id)
