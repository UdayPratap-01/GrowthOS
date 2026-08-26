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
    # DEMO_DATA | DEMO_EXECUTION | REAL_EXECUTION — never infer this from `success`.
    execution_mode: str = "REAL_EXECUTION"

    @property
    def is_real_publish(self) -> bool:
        """True only when a platform confirmed the write and returned an ID."""
        return bool(self.success and not self.demo and self.external_id)


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
