from __future__ import annotations

from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.marketing import Competitor
from app.schemas.competitor import CompetitorCreate, CompetitorOut, CompetitorUpdate
from app.services.client_service import ClientService


class CompetitorService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db
        self.clients = ClientService(db)

    async def list(self, organization_id: UUID, client_id: UUID) -> list[CompetitorOut]:
        await self.clients.get_client(organization_id, client_id)
        rows = list(
            (
                await self.db.execute(
                    select(Competitor)
                    .where(Competitor.organization_id == organization_id, Competitor.client_id == client_id)
                    .order_by(Competitor.name.asc())
                )
            ).scalars().all()
        )
        return [CompetitorOut.model_validate(r) for r in rows]

    async def create(self, organization_id: UUID, client_id: UUID, data: CompetitorCreate) -> CompetitorOut:
        await self.clients.get_client(organization_id, client_id)
        row = Competitor(organization_id=organization_id, client_id=client_id, **data.model_dump())
        self.db.add(row)
        await self.db.flush()
        await self.db.refresh(row)
        return CompetitorOut.model_validate(row)

    async def update(
        self, organization_id: UUID, client_id: UUID, competitor_id: UUID, data: CompetitorUpdate
    ) -> CompetitorOut:
        row = await self.db.scalar(
            select(Competitor).where(
                Competitor.id == competitor_id,
                Competitor.organization_id == organization_id,
                Competitor.client_id == client_id,
            )
        )
        if not row:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Competitor not found")
        for key, value in data.model_dump(exclude_unset=True).items():
            setattr(row, key, value)
        await self.db.flush()
        await self.db.refresh(row)
        return CompetitorOut.model_validate(row)

    async def delete(self, organization_id: UUID, client_id: UUID, competitor_id: UUID) -> None:
        row = await self.db.scalar(
            select(Competitor).where(
                Competitor.id == competitor_id,
                Competitor.organization_id == organization_id,
                Competitor.client_id == client_id,
            )
        )
        if not row:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Competitor not found")
        await self.db.delete(row)
