from uuid import UUID

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import AuthContext, get_current_auth
from app.db.session import get_db
from app.models.client import Client
from app.models.leads import Lead
from app.schemas.lead import LeadOut

router = APIRouter(prefix="/lead-scoring", tags=["lead-scoring"])


class LeadScoreSummary(BaseModel):
    total_leads: int
    scored_leads: int
    average_score: float | None
    high_intent: int
    medium_intent: int
    low_intent: int
    top_leads: list[LeadOut]
    data_note: str


@router.get("", response_model=LeadScoreSummary)
async def lead_score_overview(
    client_id: UUID | None = Query(default=None),
    auth: AuthContext = Depends(get_current_auth),
    db: AsyncSession = Depends(get_db),
) -> LeadScoreSummary:
    stmt = select(Lead).where(Lead.organization_id == auth.organization_id)
    if client_id:
        # enforce tenant + client ownership
        exists = await db.scalar(
            select(Client.id).where(Client.id == client_id, Client.organization_id == auth.organization_id)
        )
        if not exists:
            from fastapi import HTTPException, status

            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Client not found")
        stmt = stmt.where(Lead.client_id == client_id)
    leads = list((await db.execute(stmt)).scalars().all())
    scored = sorted([l for l in leads if l.lead_score is not None], key=lambda l: l.lead_score or 0, reverse=True)
    leads = scored + [l for l in leads if l.lead_score is None]
    avg = round(sum(l.lead_score for l in scored) / len(scored), 1) if scored else None
    return LeadScoreSummary(
        total_leads=len(leads),
        scored_leads=len(scored),
        average_score=avg,
        high_intent=len([l for l in scored if (l.lead_score or 0) >= 75]),
        medium_intent=len([l for l in scored if 50 <= (l.lead_score or 0) < 75]),
        low_intent=len([l for l in scored if (l.lead_score or 0) < 50]),
        top_leads=[LeadOut.model_validate(l) for l in scored[:10]],
        data_note=(
            "Deterministic rule-based scoring — no AI model is used. Scores are computed from recorded CRM "
            "fields only. Website visits, pricing-page views, email opens and form behaviour are not tracked "
            "and are never inferred."
        ),
    )
