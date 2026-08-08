from __future__ import annotations

import json

import httpx
from pydantic import BaseModel

from app.ai.providers.base import AIProvider, AIResponse, Message
from app.core.config import get_settings


class OpenAIProvider(AIProvider):
    name = "openai"

    async def complete(
        self,
        messages: list[Message],
        *,
        schema: type[BaseModel] | None = None,
        temperature: float = 0.4,
    ) -> AIResponse:
        settings = get_settings()
        if not settings.openai_api_key:
            raise RuntimeError("OPENAI_API_KEY is not configured")

        system_extra = ""
        if schema:
            system_extra = (
                f" Respond with valid JSON matching this schema: {schema.model_json_schema()}."
                " Never invent metrics. If data is unavailable, state Insufficient data."
            )

        api_messages = [{"role": m.role, "content": m.content} for m in messages]
        if system_extra:
            api_messages.insert(0, {"role": "system", "content": system_extra})

        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(
                "https://api.openai.com/v1/chat/completions",
                headers={"Authorization": f"Bearer {settings.openai_api_key}"},
                json={
                    "model": "gpt-4o-mini",
                    "temperature": temperature,
                    "messages": api_messages,
                    "response_format": {"type": "json_object"} if schema else None,
                },
            )
            resp.raise_for_status()
            data = resp.json()
            content = data["choices"][0]["message"]["content"]
            if schema:
                schema.model_validate(json.loads(content))
            return AIResponse(content=content, raw=data, provider=self.name)
