"""
Usage metering for AI provider calls.

Every service builds its orchestrator through `get_orchestrator()` with no
organization argument, and threading one through a dozen call sites would be a
refactor out of proportion to the goal. The organization is already bound to the
request context for logging, so the wrapper reads it from there.

Metering writes on its own session and commits immediately. Consumption is a
fact about the provider's meter, not about our transaction: if the surrounding
request later rolls back, the tokens were still spent and still cost money.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from pydantic import BaseModel

from app.ai.providers.base import AIProvider, AIResponse, Message

logger = logging.getLogger("growthos.usage")


class MeteredProvider(AIProvider):
    """Wraps a provider and records what each call consumed."""

    def __init__(self, inner: AIProvider) -> None:
        self.inner = inner
        self.name = inner.name

    async def complete(
        self,
        messages: list[Message],
        *,
        schema: type[BaseModel] | None = None,
        temperature: float = 0.4,
    ) -> AIResponse:
        from app.observability import events, metrics

        try:
            response = await self.inner.complete(messages, schema=schema, temperature=temperature)
        except Exception as exc:
            metrics.record_ai(provider=self.name, operation="complete", success=False)
            events.ai_generation(
                provider=self.name,
                operation="complete",
                success=False,
                detail=type(exc).__name__,
            )
            raise

        metrics.record_ai(provider=self.name, operation="complete", success=True)
        events.ai_generation(provider=self.name, operation="complete", success=True)
        await _record(self.name, response)
        return response


async def _record(provider: str, response: AIResponse) -> None:
    from app.observability.logging import organization_id_var
    from app.services.usage_service import Metric

    raw_org = organization_id_var.get()
    if not raw_org:
        # A call outside any tenant context — a startup probe or a test. There
        # is nobody to bill; guessing an organization would be worse than not
        # recording it.
        return

    try:
        organization_id = uuid.UUID(str(raw_org))
    except (TypeError, ValueError):
        return

    from app.services.usage_service import PendingUsage, queue_usage

    # A retried job genuinely calls the provider again and is genuinely charged
    # again, so each call gets its own key rather than being deduplicated.
    call_id = uuid.uuid4().hex
    details: dict[str, Any] = {"provider": provider}
    if response.model:
        details["model"] = response.model

    entries = [PendingUsage(organization_id, Metric.AI_REQUEST, 1, f"ai:{call_id}", None, details)]
    if response.total_tokens:
        entries.append(
            PendingUsage(
                organization_id,
                Metric.AI_TOKENS,
                response.total_tokens,
                f"ai_tokens:{call_id}",
                None,
                details,
            )
        )

    for entry in entries:
        if not queue_usage(entry):
            await _write_now(entry)


async def _write_now(entry) -> None:
    """No buffer active — a script or a direct service call. Write immediately."""
    from app.db.session import AsyncSessionLocal
    from app.services.usage_service import UsageService

    try:
        async with AsyncSessionLocal() as db:
            await UsageService(db).record(
                organization_id=entry.organization_id,
                metric=entry.metric,
                quantity=entry.quantity,
                idempotency_key=entry.idempotency_key,
                details=entry.details,
            )
            await db.commit()
    except Exception:
        # Never fail a generation because the meter could not be written.
        logger.exception("Failed to meter AI usage", extra={"event": "usage.error"})
