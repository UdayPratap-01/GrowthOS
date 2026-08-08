from decimal import Decimal
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.leads import Lead
from app.models.marketing import AnalyticsDaily
from app.models.organization import Organization
from app.repositories.client_repo import ClientRepository
from app.schemas.client import ClientContext, ClientCreate, ClientOut, ClientUpdate
from app.security.audit import write_audit


class ClientService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db
        self.repo = ClientRepository(db)

    async def list_clients(self, organization_id: UUID, q: str | None = None, industry: str | None = None) -> list[ClientOut]:
        clients = await self.repo.list(organization_id, q=q, industry=industry)
        return [ClientOut.model_validate(c) for c in clients]

    async def create_client(self, organization_id: UUID, user_id: UUID, data: ClientCreate) -> ClientOut:
        client = await self.repo.create(organization_id, data)
        await write_audit(
            self.db,
            action="client.create",
            organization_id=organization_id,
            user_id=user_id,
            resource_type="client",
            resource_id=str(client.id),
        )
        return ClientOut.model_validate(client)

    async def get_client(self, organization_id: UUID, client_id: UUID) -> ClientOut:
        client = await self.repo.get(organization_id, client_id)
        if not client:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Client not found")
        return ClientOut.model_validate(client)

    async def update_client(self, organization_id: UUID, user_id: UUID, client_id: UUID, data: ClientUpdate) -> ClientOut:
        client = await self.repo.get(organization_id, client_id)
        if not client:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Client not found")
        client = await self.repo.update(client, data)
        await write_audit(
            self.db,
            action="client.update",
            organization_id=organization_id,
            user_id=user_id,
            resource_type="client",
            resource_id=str(client.id),
        )
        return ClientOut.model_validate(client)

    async def archive_client(self, organization_id: UUID, user_id: UUID, client_id: UUID) -> ClientOut:
        client = await self.repo.get(organization_id, client_id)
        if not client:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Client not found")
        client = await self.repo.archive(client)
        await write_audit(
            self.db,
            action="client.archive",
            organization_id=organization_id,
            user_id=user_id,
            resource_type="client",
            resource_id=str(client.id),
        )
        return ClientOut.model_validate(client)

    async def build_client_context(self, organization: Organization, client_id: UUID) -> ClientContext:
        client = await self.repo.get(organization.id, client_id)
        if not client:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Client not found")

        metrics_row = await self.db.execute(
            select(
                func.coalesce(func.sum(AnalyticsDaily.spend), 0),
                func.coalesce(func.sum(AnalyticsDaily.leads), 0),
                func.coalesce(func.sum(AnalyticsDaily.revenue), 0),
                func.coalesce(func.sum(AnalyticsDaily.clicks), 0),
                func.coalesce(func.sum(AnalyticsDaily.impressions), 0),
                func.coalesce(func.sum(AnalyticsDaily.conversions), 0),
            ).where(
                AnalyticsDaily.organization_id == organization.id,
                AnalyticsDaily.client_id == client_id,
            )
        )
        spend, leads, revenue, clicks, impressions, conversions = metrics_row.one()
        lead_count = await self.db.scalar(
            select(func.count()).select_from(Lead).where(Lead.organization_id == organization.id, Lead.client_id == client_id)
        )

        available: dict = {}
        insufficient: list[str] = []
        if spend and Decimal(spend) > 0:
            available["spend"] = float(spend)
        else:
            insufficient.append("spend")
        if leads:
            available["leads"] = int(leads)
        elif lead_count:
            available["leads"] = int(lead_count)
        else:
            insufficient.append("leads")
        if revenue and Decimal(revenue) > 0:
            available["revenue"] = float(revenue)
        else:
            insufficient.append("revenue")
        if impressions:
            available["impressions"] = int(impressions)
            available["clicks"] = int(clicks or 0)
            available["conversions"] = int(conversions or 0)
            if impressions and clicks:
                available["ctr"] = round(float(clicks) / float(impressions) * 100, 2)
            if leads and spend and Decimal(spend) > 0:
                available["cpl"] = round(float(spend) / float(leads), 2)
        else:
            insufficient.extend(["impressions", "ctr", "campaign_breakdown"])

        return ClientContext(
            client_id=client.id,
            organization_id=organization.id,
            business_name=client.business_name,
            industry=client.industry,
            website=client.website,
            description=client.description,
            location=client.location,
            target_audience=client.target_audience,
            products_services=client.products_services,
            marketing_goals=client.marketing_goals,
            monthly_budget=client.monthly_budget,
            brand_voice=client.brand_voice,
            competitors=client.competitors or [],
            primary_channels=client.primary_channels or [],
            kpis=client.kpis or [],
            demo_mode=organization.demo_mode,
            available_metrics=available,
            insufficient_data_fields=insufficient,
        )
