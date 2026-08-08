from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.client import Client
from app.models.enums import ClientStatus
from app.schemas.client import ClientCreate, ClientUpdate


class ClientRepository:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def list(
        self,
        organization_id: UUID,
        *,
        q: str | None = None,
        industry: str | None = None,
        status: ClientStatus | None = ClientStatus.active,
    ) -> list[Client]:
        stmt = select(Client).where(Client.organization_id == organization_id)
        if status:
            stmt = stmt.where(Client.status == status)
        if industry:
            stmt = stmt.where(Client.industry.ilike(f"%{industry}%"))
        if q:
            like = f"%{q}%"
            stmt = stmt.where(
                or_(
                    Client.business_name.ilike(like),
                    Client.industry.ilike(like),
                    Client.location.ilike(like),
                )
            )
        stmt = stmt.order_by(Client.business_name.asc())
        result = await self.db.execute(stmt)
        return list(result.scalars().all())

    async def get(self, organization_id: UUID, client_id: UUID) -> Client | None:
        result = await self.db.execute(
            select(Client).where(Client.id == client_id, Client.organization_id == organization_id)
        )
        return result.scalar_one_or_none()

    async def create(self, organization_id: UUID, data: ClientCreate) -> Client:
        client = Client(organization_id=organization_id, **data.model_dump())
        self.db.add(client)
        await self.db.flush()
        await self.db.refresh(client)
        return client

    async def update(self, client: Client, data: ClientUpdate) -> Client:
        for key, value in data.model_dump(exclude_unset=True).items():
            setattr(client, key, value)
        await self.db.flush()
        await self.db.refresh(client)
        return client

    async def archive(self, client: Client) -> Client:
        client.status = ClientStatus.archived
        client.archived_at = datetime.now(timezone.utc)
        await self.db.flush()
        await self.db.refresh(client)
        return client
