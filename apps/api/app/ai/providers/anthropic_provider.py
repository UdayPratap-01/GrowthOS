from __future__ import annotations

import json

import httpx
from pydantic import BaseModel

from app.ai.providers.base import AIProvider, AIResponse, Message
from app.core.config import get_settings


class AnthropicProvider(AIProvider):
    name = "anthropic"

    async def complete(
        self,
        messages: list[Message],
        *,
        schema: type[BaseModel] | None = None,
        temperature: float = 0.4,
    ) -> AIResponse:
        settings = get_settings()
        if not settings.anthropic_api_key:
            raise RuntimeError("ANTHROPIC_API_KEY is not configured")

        system = "You are GrowthOS AI. Never invent metrics. If data is unavailable, say Insufficient data."
        if schema:
            system += f" Return JSON matching: {schema.model_json_schema()}"

        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "x-api-key": settings.anthropic_api_key,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                json={
                    "model": "claude-3-5-haiku-latest",
                    "max_tokens": 2000,
                    "temperature": temperature,
                    "system": system,
                    "messages": [{"role": m.role, "content": m.content} for m in messages if m.role != "system"],
                },
            )
            resp.raise_for_status()
            data = resp.json()
            content = data["content"][0]["text"]
            if schema:
                schema.model_validate(json.loads(content))
            return AIResponse(content=content, raw=data, provider=self.name)
