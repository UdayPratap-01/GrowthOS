"""Future-phase stubs — honest not_connected / demo_data only."""

from __future__ import annotations

from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.integrations.base import (
    ConnectResult,
    ConnectionStatus,
    IntegrationConnectionStatus,
    MarketingIntegration,
    SyncResult,
)
from app.integrations.persistence import get_integration_row


class FutureStubIntegration(MarketingIntegration):
    def __init__(self, provider: str, display_name: str, phase: str = "Phase 5") -> None:
        self.provider = provider
        self.display_name = display_name
        self.phase = phase

    def credentials_configured(self) -> bool:
        return False

    async def get_connection_status(self, organization_id: UUID, client_id: UUID | None = None) -> ConnectionStatus:
        db: AsyncSession = self._db  # type: ignore[attr-defined]
        row = await get_integration_row(db, organization_id=organization_id, provider=self.provider, client_id=client_id)
        settings = get_settings()
        if row and row.status == "connected" and row.secret_ref:
            return ConnectionStatus(
                provider=self.provider,
                status=IntegrationConnectionStatus.connected,
                message=f"{self.display_name} connected.",
                last_synced_at=(row.config or {}).get("last_synced_at"),
                credentials_configured=False,
                can_connect=False,
            )
        if settings.demo_mode:
            return ConnectionStatus(
                provider=self.provider,
                status=IntegrationConnectionStatus.demo_data,
                message=f"{self.display_name} ships in {self.phase}. Demo data only — not a live connection.",
                credentials_configured=False,
                can_connect=False,
            )
        return ConnectionStatus(
            provider=self.provider,
            status=IntegrationConnectionStatus.not_connected,
            message=f"{self.display_name} is planned for {self.phase}.",
            credentials_configured=False,
            can_connect=False,
        )

    async def build_authorize_url(
        self, *, organization_id: UUID, user_id: UUID, client_id: UUID | None
    ) -> ConnectResult:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"{self.display_name} OAuth is not available until {self.phase}.",
        )

    async def handle_callback(self, *, code: str, state: str) -> dict:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Not implemented")

    async def disconnect(self, organization_id: UUID, client_id: UUID | None = None) -> ConnectionStatus:
        return await self.get_connection_status(organization_id, client_id)

    async def sync(self, organization_id: UUID, client_id: UUID | None = None) -> SyncResult:
        status_now = await self.get_connection_status(organization_id, client_id)
        return SyncResult(
            provider=self.provider,
            success=False,
            status=status_now.status,
            message=f"Live sync for {self.display_name} is not implemented ({self.phase}).",
            errors=["not_implemented"],
        )
