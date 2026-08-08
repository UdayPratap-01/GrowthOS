from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass
class PublishResult:
    success: bool
    status: str
    message: str
    external_id: str | None = None
    platform_response: dict[str, Any] = field(default_factory=dict)
    error: str | None = None
    demo: bool = False


class SocialPublisher(ABC):
    platform: str

    @abstractmethod
    def credentials_ready(self, *, organization_id, client_id) -> bool:
        raise NotImplementedError

    @abstractmethod
    async def publish(self, *, content: dict, organization_id, client_id) -> PublishResult:
        raise NotImplementedError

    @abstractmethod
    async def schedule(self, *, content: dict, scheduled_for: str, organization_id, client_id) -> PublishResult:
        raise NotImplementedError

    @abstractmethod
    async def get_status(self, *, external_id: str, organization_id, client_id) -> PublishResult:
        raise NotImplementedError

    @abstractmethod
    async def delete(self, *, external_id: str, organization_id, client_id) -> PublishResult:
        raise NotImplementedError
