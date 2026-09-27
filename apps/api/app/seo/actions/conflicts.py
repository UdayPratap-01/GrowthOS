"""Conflict detection for SEO actions (M9.13)."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.automation import AIAction
from app.models.enums import AIActionStatus, AIActionType

_ACTIVE_STATUSES = {
    AIActionStatus.pending,
    AIActionStatus.approved,
    AIActionStatus.executing,
    AIActionStatus.scheduled,
}


async def find_conflicting_action(
    db: AsyncSession,
    *,
    organization_id: UUID,
    action_type: AIActionType,
    source_id: UUID,
) -> AIAction | None:
    """Return an active action for the same SEO source artifact, if any."""
    rows = (
        await db.execute(
            select(AIAction)
            .where(
                AIAction.organization_id == organization_id,
                AIAction.action_type == action_type,
                AIAction.status.in_(list(_ACTIVE_STATUSES)),
            )
            .order_by(AIAction.created_at.desc())
            .limit(50)
        )
    ).scalars().all()
    source_key = str(source_id)
    for row in rows:
        payload = row.payload or {}
        if payload.get("source_id") == source_key:
            return row
    return None
