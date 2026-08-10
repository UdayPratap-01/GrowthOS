from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.agents.lead_agent import LeadScoreRequest
from app.ai.orchestrator import get_orchestrator
from app.services.usage_service import Metric, meter
from app.models.enums import LeadStatus
from app.models.organization import Organization
from app.repositories.lead_repo import LeadRepository
from app.schemas.lead import LeadCreate, LeadOut, LeadUpdate
from app.services.client_service import ClientService


class LeadService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db
        self.repo = LeadRepository(db)
        self.clients = ClientService(db)
        self.orchestrator = get_orchestrator()

    async def list_leads(self, organization_id: UUID, client_id: UUID, status: LeadStatus | None = None) -> list[LeadOut]:
        leads = await self.repo.list(organization_id, client_id, status=status)
        return [LeadOut.model_validate(l) for l in leads]

    async def create_lead(self, organization: Organization, client_id: UUID, data: LeadCreate) -> LeadOut:
        context = await self.clients.build_client_context(organization, client_id)
        score = self.orchestrator.score_lead_deterministic(
            context,
            LeadScoreRequest(
                name=data.name,
                email=data.email,
                phone=data.phone,
                source=data.source,
                campaign=data.campaign,
                ad=data.ad,
                status=data.status.value,
                notes=data.notes,
            ),
        )
        lead = await self.repo.create(
            organization.id,
            client_id,
            data,
            score=score.score,
            explanation=score.model_dump(),
        )
        await meter(
            self.db,
            organization_id=organization.id,
            metric=Metric.LEAD,
            idempotency_key=f"lead:{lead.id}",
            client_id=client_id,
        )
        return LeadOut.model_validate(lead)

    async def update_lead(self, organization_id: UUID, client_id: UUID, lead_id: UUID, data: LeadUpdate) -> LeadOut:
        lead = await self.repo.get(organization_id, client_id, lead_id)
        if not lead:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Lead not found")
        lead = await self.repo.update(lead, data)
        return LeadOut.model_validate(lead)

    async def delete_lead(self, organization_id: UUID, client_id: UUID, lead_id: UUID) -> None:
        lead = await self.repo.get(organization_id, client_id, lead_id)
        if not lead:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Lead not found")
        await self.repo.delete(lead)

    async def rescore(self, organization: Organization, client_id: UUID, lead_id: UUID) -> LeadOut:
        lead = await self.repo.get(organization.id, client_id, lead_id)
        if not lead:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Lead not found")
        context = await self.clients.build_client_context(organization, client_id)
        score = self.orchestrator.score_lead_deterministic(
            context,
            LeadScoreRequest(
                name=lead.name,
                email=lead.email,
                phone=lead.phone,
                source=lead.source,
                campaign=lead.campaign,
                ad=lead.ad,
                status=lead.status.value,
                notes=lead.notes,
            ),
        )
        lead.lead_score = score.score
        lead.score_explanation = score.model_dump()
        await self.db.flush()
        await self.db.refresh(lead)
        return LeadOut.model_validate(lead)
