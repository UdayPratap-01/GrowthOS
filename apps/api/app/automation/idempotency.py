"""Idempotency helpers for AI action execution."""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.automation import AIAction
from app.models.enums import AIActionStatus
from app.publishing.provider_errors import reconciliation_blocks_retry

def build_action_idempotency_key(
    *,
    organization_id: UUID,
    action_type: str,
    target_id: str | None,
    payload: dict | None,
    explicit: str | None = None,
) -> str:
    if explicit:
        return explicit.strip()[:255]
    parts = [
        str(organization_id),
        action_type,
        target_id or "",
        str(sorted((payload or {}).items())),
    ]
    digest = hashlib.sha256("|".join(parts).encode()).hexdigest()[:32]
    return f"action:{digest}"


def execution_idempotency_key(action_id: UUID) -> str:
    return f"exec:{action_id}"


async def find_action_by_idempotency(
    db: AsyncSession,
    *,
    organization_id: UUID,
    idempotency_key: str,
) -> AIAction | None:
    """Return an existing action for this idempotency key, if any."""
    return await db.scalar(
        select(AIAction)
        .where(
            AIAction.organization_id == organization_id,
            AIAction.idempotency_key == idempotency_key,
        )
        .order_by(AIAction.created_at.desc())
        .limit(1)
    )


async def find_completed_by_idempotency(
    db: AsyncSession,
    *,
    organization_id: UUID,
    idempotency_key: str,
) -> AIAction | None:
    return await db.scalar(
        select(AIAction).where(
            AIAction.organization_id == organization_id,
            AIAction.idempotency_key == idempotency_key,
            AIAction.status == AIActionStatus.completed,
        )
    )


async def try_claim_action_for_execution(
    db: AsyncSession,
    action: AIAction,
    *,
    force: bool = False,
) -> str:
    """
    Atomically transition to EXECUTING.

    Returns:
        "claimed" — this caller owns execution
        "completed" — already finished (idempotent no-op)
        "executing" — another caller is in progress
        "blocked" — status prevents execution
    """
    if action.status == AIActionStatus.completed and not force:
        return "completed"

    if action.status == AIActionStatus.failed and reconciliation_blocks_retry(action) and not force:
        return "blocked"

    claimable = {AIActionStatus.approved, AIActionStatus.failed}
    if force:
        claimable.add(AIActionStatus.approved)
    # Auto-execute path may pass pending when approval not required.
    if not action.requires_approval or force:
        claimable.add(AIActionStatus.pending)

    result = await db.execute(
        update(AIAction)
        .where(
            AIAction.id == action.id,
            AIAction.organization_id == action.organization_id,
            AIAction.status.in_(list(claimable)),
        )
        .values(
            status=AIActionStatus.executing,
            error=None,
            executing_at=datetime.now(timezone.utc),
        )
    )
    if result.rowcount == 1:
        action.status = AIActionStatus.executing
        action.error = None
        action.executing_at = datetime.now(timezone.utc)
        return "claimed"

    await db.refresh(action)
    if action.status == AIActionStatus.completed:
        return "completed"
    if action.status == AIActionStatus.executing:
        return "executing"
    return "blocked"


def sanitize_platform_response(data: dict | None) -> dict:
    """Strip credential-like fields before persistence."""
    if not data:
        return {}
    blocked = {"access_token", "refresh_token", "client_secret", "authorization", "token"}
    clean: dict = {}
    for key, value in data.items():
        if key.lower() in blocked:
            continue
        if isinstance(value, dict):
            nested = sanitize_platform_response(value)
            if nested:
                clean[key] = nested
        else:
            clean[key] = value
    return clean
