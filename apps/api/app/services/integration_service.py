from __future__ import annotations

from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.integrations.base import ConnectResult, ConnectionStatus, SyncResult
from app.integrations.registry import get_integration, list_integrations


class IntegrationService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    def _bind(self, provider: str):
        integration = get_integration(provider)
        if not integration:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Unknown integration provider")
        integration._db = self.db  # type: ignore[attr-defined]
        return integration

    async def list_statuses(self, organization_id: UUID, client_id: UUID | None = None) -> list[ConnectionStatus]:
        out: list[ConnectionStatus] = []
        for integration in list_integrations():
            integration._db = self.db  # type: ignore[attr-defined]
            out.append(await integration.get_connection_status(organization_id, client_id))
        return out

    async def connect(
        self, provider: str, *, organization_id: UUID, user_id: UUID, client_id: UUID | None
    ) -> ConnectResult:
        return await self._bind(provider).build_authorize_url(
            organization_id=organization_id, user_id=user_id, client_id=client_id
        )

    async def callback(self, provider: str, *, code: str, state: str) -> dict:
        return await self._bind(provider).handle_callback(code=code, state=state)

    async def disconnect(
        self, provider: str, *, organization_id: UUID, client_id: UUID | None
    ) -> ConnectionStatus:
        return await self._bind(provider).disconnect(organization_id, client_id)

    async def sync(self, provider: str, *, organization_id: UUID, client_id: UUID | None) -> SyncResult:
        return await self._bind(provider).sync(organization_id, client_id)
