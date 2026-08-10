from __future__ import annotations

import json

import httpx
from pydantic import BaseModel, ValidationError

from app.ai.providers.base import AIGenerationError, AIProvider, AIResponse, Message
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

        try:
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
        except httpx.HTTPStatusError as exc:
            raise AIGenerationError(
                f"OpenAI request failed with HTTP {exc.response.status_code}.", provider=self.name
            ) from exc
        except httpx.HTTPError as exc:
            raise AIGenerationError(f"OpenAI request failed: {exc}", provider=self.name) from exc
        except (KeyError, IndexError, ValueError) as exc:
            raise AIGenerationError(f"OpenAI returned an unreadable response: {exc}", provider=self.name) from exc

        if schema:
            try:
                schema.model_validate(json.loads(content))
            except (ValidationError, json.JSONDecodeError) as exc:
                raise AIGenerationError(
                    f"OpenAI response did not match the expected schema: {exc}", provider=self.name
                ) from exc
        usage = data.get("usage") or {}
        return AIResponse(
            content=content,
            raw=data,
            provider=self.name,
            model=data.get("model"),
            input_tokens=usage.get("prompt_tokens"),
            output_tokens=usage.get("completion_tokens"),
        )
