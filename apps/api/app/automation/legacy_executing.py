"""Safe operator path for legacy EXECUTING rows with executing_at=NULL.

The automatic stale reaper intentionally skips these rows. Operators must choose
an explicit outcome — never blind re-execution.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.automation import ActionExecution, AIAction
from app.models.enums import AIActionStatus
from app.publishing.provider_errors import ReconciliationState
from app.security.audit import write_audit


class LegacyRecoveryAction(str, Enum):
    mark_failed = "MARK_FAILED"
    mark_unknown = "MARK_UNKNOWN"
    leave_executing = "LEAVE_EXECUTING"


def _aware(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


async def list_legacy_executing(
    db: AsyncSession,
    *,
    organization_id: UUID,
    limit: int = 100,
) -> list[dict]:
    rows = list(
        (
            await db.scalars(
                select(AIAction)
                .where(
                    AIAction.organization_id == organization_id,
                    AIAction.status == AIActionStatus.executing,
                    AIAction.executing_at.is_(None),
                )
                .order_by(AIAction.updated_at.asc())
                .limit(limit)
            )
        ).all()
    )
    now = datetime.now(timezone.utc)
    out: list[dict] = []
    for action in rows:
        updated = _aware(action.updated_at) or _aware(action.created_at)
        age_seconds = int((now - updated).total_seconds()) if updated else None
        exec_row = await db.scalar(
            select(ActionExecution)
            .where(
                ActionExecution.action_id == action.id,
                ActionExecution.organization_id == organization_id,
            )
            .order_by(ActionExecution.created_at.desc())
            .limit(1)
        )
        out.append(
            {
                "action_id": str(action.id),
                "organization_id": str(action.organization_id),
                "client_id": str(action.client_id) if action.client_id else None,
                "action_type": action.action_type.value,
                "platform": action.platform,
                "external_id": action.external_id,
                "status": action.status.value,
                "executing_at": None,
                "updated_at": updated.isoformat() if updated else None,
                "age_seconds": age_seconds,
                "has_execution_record": exec_row is not None,
                "execution_id": str(exec_row.id) if exec_row else None,
                "error": action.error,
            }
        )
    return out


async def recover_legacy_executing(
    db: AsyncSession,
    *,
    organization_id: UUID,
    action_id: UUID,
    recovery: LegacyRecoveryAction,
    actor_user_id: UUID,
    reason: str,
) -> AIAction:
    """
    Explicit operator recovery. Never re-executes.
    """
    reason_clean = (reason or "").strip()[:1000]
    if not reason_clean:
        raise ValueError("REASON_REQUIRED")

    action = await db.scalar(
        select(AIAction).where(
            AIAction.id == action_id,
            AIAction.organization_id == organization_id,
        )
    )
    if action is None:
        raise LookupError("ACTION_NOT_FOUND")
    if action.status != AIActionStatus.executing:
        raise ValueError("NOT_EXECUTING")
    if action.executing_at is not None:
        raise ValueError("NOT_LEGACY_NULL_EXECUTING_AT")

    if recovery == LegacyRecoveryAction.leave_executing:
        await write_audit(
            db,
            action="legacy_action.reviewed",
            organization_id=organization_id,
            user_id=actor_user_id,
            resource_type="ai_action",
            resource_id=str(action_id),
            details={
                "trigger": "operator",
                "recovery": recovery.value,
                "reason": reason_clean,
                "left_status": AIActionStatus.executing.value,
            },
        )
        return action

    now = datetime.now(timezone.utc)
    if recovery == LegacyRecoveryAction.mark_failed:
        values = {
            "status": AIActionStatus.failed,
            "error": f"LEGACY_EXECUTING_RECOVERED: {reason_clean}",
            "executing_at": None,
            "result": {
                **(action.result or {}),
                "legacy_recovery": {
                    "recovery": recovery.value,
                    "recovered_at": now.isoformat(),
                    "resolver_user_id": str(actor_user_id),
                    "reason": reason_clean,
                },
            },
        }
    elif recovery == LegacyRecoveryAction.mark_unknown:
        recon = {
            "state": ReconciliationState.unknown.value,
            "provider": (action.platform or "").lower(),
            "operation": action.action_type.value,
            "external_id": action.external_id,
            "ambiguous_error_code": "LEGACY_EXECUTING_UNKNOWN",
            "ambiguous_since": now.isoformat(),
            "last_checked_at": now.isoformat(),
            "message": reason_clean,
            "manual_resolution": None,
        }
        values = {
            "status": AIActionStatus.failed,
            "error": f"PROVIDER_STATE_UNKNOWN: legacy EXECUTING — {reason_clean}",
            "executing_at": None,
            "result": {
                **(action.result or {}),
                "reconciliation": recon,
                "legacy_recovery": {
                    "recovery": recovery.value,
                    "recovered_at": now.isoformat(),
                    "resolver_user_id": str(actor_user_id),
                    "reason": reason_clean,
                },
            },
        }
    else:
        raise ValueError("INVALID_RECOVERY")

    update_result = await db.execute(
        update(AIAction)
        .where(
            AIAction.id == action_id,
            AIAction.organization_id == organization_id,
            AIAction.status == AIActionStatus.executing,
            AIAction.executing_at.is_(None),
        )
        .values(**values)
        .execution_options(synchronize_session=False)
    )
    if update_result.rowcount != 1:
        raise RuntimeError("CONCURRENT_RECOVERY")

    # Close open execution rows without inventing provider success
    open_execs = list(
        (
            await db.scalars(
                select(ActionExecution).where(
                    ActionExecution.action_id == action_id,
                    ActionExecution.organization_id == organization_id,
                    ActionExecution.finished_at.is_(None),
                )
            )
        ).all()
    )
    for ex in open_execs:
        ex.finished_at = now
        ex.status = AIActionStatus.failed
        ex.error_code = "LEGACY_EXECUTING_RECOVERED"
        ex.error_message = str(values.get("error") or reason_clean)[:2000]

    await write_audit(
        db,
        action="legacy_action.recovered",
        organization_id=organization_id,
        user_id=actor_user_id,
        resource_type="ai_action",
        resource_id=str(action_id),
        details={
            "trigger": "operator",
            "recovery": recovery.value,
            "reason": reason_clean,
            "new_status": values["status"].value
            if isinstance(values.get("status"), AIActionStatus)
            else values.get("status"),
        },
    )
    await db.flush()
    # synchronize_session=False leaves the identity map stale — force a fresh load.
    db.expire_all()
    updated = await db.scalar(
        select(AIAction).where(AIAction.id == action_id, AIAction.organization_id == organization_id)
    )
    assert updated is not None
    return updated
