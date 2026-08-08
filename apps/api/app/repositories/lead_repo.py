from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.enums import LeadStatus
from app.models.leads import Lead
from app.schemas.lead import LeadCreate, LeadUpdate


class LeadRepository:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def list(self, organization_id: UUID, client_id: UUID, status: LeadStatus | None = None) -> list[Lead]:
        stmt = select(Lead).where(Lead.organization_id == organization_id, Lead.client_id == client_id)
        if status:
            stmt = stmt.where(Lead.status == status)
        stmt = stmt.order_by(Lead.created_at.desc())
        result = await self.db.execute(stmt)
        return list(result.scalars().all())

    async def get(self, organization_id: UUID, client_id: UUID, lead_id: UUID) -> Lead | None:
        result = await self.db.execute(
            select(Lead).where(
                Lead.id == lead_id,
                Lead.organization_id == organization_id,
                Lead.client_id == client_id,
            )
        )
        return result.scalar_one_or_none()

    async def create(self, organization_id: UUID, client_id: UUID, data: LeadCreate, score: int | None, explanation: dict) -> Lead:
        lead = Lead(
            organization_id=organization_id,
            client_id=client_id,
            **data.model_dump(),
            lead_score=score,
            score_explanation=explanation,
            last_activity_at=datetime.now(timezone.utc),
        )
        self.db.add(lead)
        await self.db.flush()
        await self.db.refresh(lead)
        return lead

    async def update(self, lead: Lead, data: LeadUpdate) -> Lead:
        for key, value in data.model_dump(exclude_unset=True).items():
            setattr(lead, key, value)
        lead.last_activity_at = datetime.now(timezone.utc)
        await self.db.flush()
        await self.db.refresh(lead)
        return lead

    async def delete(self, lead: Lead) -> None:
        await self.db.delete(lead)
