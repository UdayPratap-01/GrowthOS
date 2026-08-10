from __future__ import annotations

import json

import httpx
from pydantic import BaseModel, ValidationError

from app.ai.providers.base import AIGenerationError, AIProvider, AIResponse, Message
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

        try:
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
        except httpx.HTTPStatusError as exc:
            raise AIGenerationError(
                f"Anthropic request failed with HTTP {exc.response.status_code}.", provider=self.name
            ) from exc
        except httpx.HTTPError as exc:
            raise AIGenerationError(f"Anthropic request failed: {exc}", provider=self.name) from exc
        except (KeyError, IndexError, ValueError) as exc:
            raise AIGenerationError(f"Anthropic returned an unreadable response: {exc}", provider=self.name) from exc

        if schema:
            try:
                schema.model_validate(json.loads(content))
            except (ValidationError, json.JSONDecodeError) as exc:
                raise AIGenerationError(
                    f"Anthropic response did not match the expected schema: {exc}", provider=self.name
                ) from exc
        usage = data.get("usage") or {}
        return AIResponse(
            content=content,
            raw=data,
            provider=self.name,
            model=data.get("model"),
            input_tokens=usage.get("input_tokens"),
            output_tokens=usage.get("output_tokens"),
        )
