from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from pydantic import BaseModel


class Message(BaseModel):
    role: str
    content: str


class AIResponse(BaseModel):
    content: str
    raw: dict[str, Any] | None = None
    provider: str


class AIProvider(ABC):
    name: str

    @abstractmethod
    async def complete(
        self,
        messages: list[Message],
        *,
        schema: type[BaseModel] | None = None,
        temperature: float = 0.4,
    ) -> AIResponse:
        raise NotImplementedError
