from __future__ import annotations

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.ai_ops import AuditLog


async def write_audit(
    db: AsyncSession,
    *,
    action: str,
    organization_id: UUID | None = None,
    user_id: UUID | None = None,
    resource_type: str | None = None,
    resource_id: str | None = None,
    ip_address: str | None = None,
    details: dict | None = None,
) -> None:
    db.add(
        AuditLog(
            action=action,
            organization_id=organization_id,
            user_id=user_id,
            resource_type=resource_type,
            resource_id=resource_id,
            ip_address=ip_address,
            details=details or {},
        )
    )
