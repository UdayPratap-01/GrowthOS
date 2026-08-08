from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.marketing import Campaign
from app.schemas.campaigns import CampaignOut


class CampaignService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def list_campaigns(
        self, organization_id: UUID, *, client_id: UUID | None = None, platform: str | None = None
    ) -> list[CampaignOut]:
        stmt = select(Campaign).where(Campaign.organization_id == organization_id).order_by(Campaign.spend.desc())
        if client_id is not None:
            stmt = stmt.where(Campaign.client_id == client_id)
        if platform:
            stmt = stmt.where(Campaign.platform == platform)
        rows = (await self.db.execute(stmt)).scalars().all()
        return [
            CampaignOut(
                id=c.id,
                client_id=c.client_id,
                ad_account_id=c.ad_account_id,
                name=c.name,
                platform=c.platform,
                status=c.status,
                objective=c.objective,
                spend=c.spend,
                metrics=c.metrics or {},
                data_source=c.data_source.value if hasattr(c.data_source, "value") else str(c.data_source),
            )
            for c in rows
        ]
