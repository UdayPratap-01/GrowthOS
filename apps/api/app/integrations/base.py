from __future__ import annotations

from abc import ABC, abstractmethod
from enum import Enum
from uuid import UUID

from pydantic import BaseModel, Field


class IntegrationConnectionStatus(str, Enum):
    connected = "connected"
    not_connected = "not_connected"
    demo_data = "demo_data"
    sync_error = "sync_error"


class ConnectionStatus(BaseModel):
    provider: str
    status: IntegrationConnectionStatus
    message: str
    last_synced_at: str | None = None
    account_label: str | None = None
    credentials_configured: bool = False
    can_connect: bool = False


class SyncResult(BaseModel):
    provider: str
    success: bool
    status: IntegrationConnectionStatus
    records_synced: int = 0
    message: str
    errors: list[str] = Field(default_factory=list)


class ConnectResult(BaseModel):
    provider: str
    authorize_url: str
    message: str


class MarketingIntegration(ABC):
    provider: str
    display_name: str

    @abstractmethod
    def credentials_configured(self) -> bool:
        raise NotImplementedError

    @abstractmethod
    async def get_connection_status(self, organization_id: UUID, client_id: UUID | None = None) -> ConnectionStatus:
        raise NotImplementedError

    @abstractmethod
    async def build_authorize_url(
        self, *, organization_id: UUID, user_id: UUID, client_id: UUID | None
    ) -> ConnectResult:
        raise NotImplementedError

    @abstractmethod
    async def handle_callback(self, *, code: str, state: str) -> dict:
        """Exchange code, persist encrypted tokens. Returns public metadata only."""
        raise NotImplementedError

    @abstractmethod
    async def disconnect(self, organization_id: UUID, client_id: UUID | None = None) -> ConnectionStatus:
        raise NotImplementedError

    @abstractmethod
    async def sync(self, organization_id: UUID, client_id: UUID | None = None) -> SyncResult:
        raise NotImplementedError
