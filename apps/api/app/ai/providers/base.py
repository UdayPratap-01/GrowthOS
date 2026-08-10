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
    #: Reported by the provider when available. Left None rather than estimated:
    #: a guessed token count on an invoice is worse than an absent one.
    model: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None

    @property
    def total_tokens(self) -> int:
        return (self.input_tokens or 0) + (self.output_tokens or 0)


class AIGenerationError(RuntimeError):
    """
    A real provider call failed. Surfaced as an explicit failure rather than
    substituted with fabricated content.
    """

    def __init__(self, message: str, *, provider: str) -> None:
        super().__init__(message)
        self.provider = provider


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
