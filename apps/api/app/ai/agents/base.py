from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Generic, TypeVar

from pydantic import BaseModel

from app.ai.providers.base import AIProvider, Message
from app.schemas.client import ClientContext

TIn = TypeVar("TIn", bound=BaseModel)
TOut = TypeVar("TOut", bound=BaseModel)


class BaseAgent(ABC, Generic[TIn, TOut]):
    name: str
    output_schema: type[TOut]

    def __init__(self, provider: AIProvider) -> None:
        self.provider = provider

    @abstractmethod
    def build_messages(self, context: ClientContext, request: TIn) -> list[Message]:
        raise NotImplementedError

    async def run(self, context: ClientContext, request: TIn) -> TOut:
        messages = self.build_messages(context, request)
        response = await self.provider.complete(messages, schema=self.output_schema)
        return self.output_schema.model_validate_json(response.content)
