from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class StoredObject:
    key: str
    url: str | None
    content_type: str | None = None
    size: int | None = None


class ObjectStorage(ABC):
    @abstractmethod
    async def put_bytes(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> StoredObject:
        raise NotImplementedError

    @abstractmethod
    async def get_bytes(self, key: str) -> bytes:
        raise NotImplementedError

    @abstractmethod
    async def delete(self, key: str) -> None:
        raise NotImplementedError

    @abstractmethod
    def public_url(self, key: str) -> str | None:
        raise NotImplementedError
