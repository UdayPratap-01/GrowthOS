"""Recover AI actions stuck in EXECUTING after a worker crash or hang."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.models.automation import ActionExecution, AIAction
from app.models.enums import AIActionStatus
from app.security.audit import write_audit

logger = logging.getLogger(__name__)

STALE_RECOVERY_ERROR_PREFIX = "STALE_EXECUTION_RECOVERED"
MIN_STALE_TIMEOUT_MINUTES = 5
MAX_STALE_TIMEOUT_MINUTES = 24 * 60
DEFAULT_STALE_RECOVERY_BATCH_SIZE = 50
MAX_STALE_RECOVERY_BATCH_SIZE = 500


def _aware_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


def validate_stale_recovery_settings(settings: Settings) -> list[str]:
    errors: list[str] = []
    timeout = settings.autonomous_execution_stale_timeout_minutes
    batch = settings.autonomous_execution_stale_recovery_batch_size
    if timeout < MIN_STALE_TIMEOUT_MINUTES:
        errors.append(
            f"AUTONOMOUS_EXECUTION_STALE_TIMEOUT_MINUTES must be at least {MIN_STALE_TIMEOUT_MINUTES}."
        )
    elif timeout > MAX_STALE_TIMEOUT_MINUTES:
        errors.append(
            f"AUTONOMOUS_EXECUTION_STALE_TIMEOUT_MINUTES must not exceed {MAX_STALE_TIMEOUT_MINUTES}."
        )
    if batch <= 0:
        errors.append("AUTONOMOUS_EXECUTION_STALE_RECOVERY_BATCH_SIZE must be positive.")
    elif batch > MAX_STALE_RECOVERY_BATCH_SIZE:
        errors.append(
            f"AUTONOMOUS_EXECUTION_STALE_RECOVERY_BATCH_SIZE must not exceed {MAX_STALE_RECOVERY_BATCH_SIZE}."
        )
    return errors


async def recover_stale_action(
    db: AsyncSession,
    *,
    action_id: UUID,
    organization_id: UUID,
    cutoff: datetime,
    now: datetime,
    timeout_minutes: int,
) -> AIAction | None:
    """
    Atomically move one stale EXECUTING action to FAILED.

    The WHERE clause re-checks status and executing_at so a newly claimed action
    cannot be recovered by a concurrent reaper.
    """
    action = await db.get(AIAction, action_id)
    if action is None or action.organization_id != organization_id:
        return None
    if action.status != AIActionStatus.executing:
        return None
    executing_at = _aware_utc(action.executing_at)
    if executing_at is None or executing_at > cutoff:
        return None

    stale_seconds = int((now - executing_at).total_seconds())
    recovery_error = (
        f"{STALE_RECOVERY_ERROR_PREFIX}: execution exceeded {timeout_minutes}m "
        f"(stale_for_seconds={stale_seconds})"
    )

    result = await db.execute(
        update(AIAction)
        .where(
            AIAction.id == action_id,
            AIAction.organization_id == organization_id,
            AIAction.status == AIActionStatus.executing,
            AIAction.executing_at.isnot(None),
            AIAction.executing_at <= cutoff,
        )
        .values(
            status=AIActionStatus.failed,
            error=recovery_error,
            executing_at=None,
            retry_count=AIAction.retry_count + 1,
        )
        .execution_options(synchronize_session=False)
    )
    if result.rowcount != 1:
        return None

    await db.execute(
        update(ActionExecution)
        .where(
            ActionExecution.action_id == action_id,
            ActionExecution.organization_id == organization_id,
            ActionExecution.status == AIActionStatus.executing,
        )
        .values(
            status=AIActionStatus.failed,
            error_code=STALE_RECOVERY_ERROR_PREFIX,
            error_message=recovery_error,
            finished_at=now,
        )
        .execution_options(synchronize_session=False)
    )

    await write_audit(
        db,
        action="ai_action.stale_recovery",
        organization_id=organization_id,
        user_id=None,
        resource_type="ai_action",
        resource_id=str(action_id),
        details={
            "trigger": "stale_recovery",
            "previous_status": AIActionStatus.executing.value,
            "new_status": AIActionStatus.failed.value,
            "executing_at": executing_at.isoformat(),
            "stale_for_seconds": stale_seconds,
            "timeout_minutes": timeout_minutes,
            "idempotency_key": action.idempotency_key,
        },
    )
    await db.flush()
    await db.refresh(action)

    logger.warning(
        "Recovered stale EXECUTING action id=%s org=%s stale_for_seconds=%d",
        action_id,
        organization_id,
        stale_seconds,
    )
    return action


async def reap_stale_executing_actions(
    db: AsyncSession,
    *,
    limit: int | None = None,
    now: datetime | None = None,
) -> list[UUID]:
    """
    Find and recover a bounded batch of stale EXECUTING actions.

    Safe for multiple workers: each action is recovered via an atomic CAS UPDATE.
    """
    settings = get_settings()
    now = now or datetime.now(timezone.utc)
    timeout_minutes = settings.autonomous_execution_stale_timeout_minutes
    batch_size = limit or settings.autonomous_execution_stale_recovery_batch_size
    cutoff = now - timedelta(minutes=timeout_minutes)

    stmt = (
        select(AIAction.id, AIAction.organization_id)
        .where(
            AIAction.status == AIActionStatus.executing,
            AIAction.executing_at.isnot(None),
            AIAction.executing_at <= cutoff,
        )
        .order_by(AIAction.executing_at.asc())
        .limit(batch_size)
    )
    if db.bind is not None and db.bind.dialect.name == "postgresql":
        stmt = stmt.with_for_update(skip_locked=True)

    candidates = (await db.execute(stmt)).all()
    if not candidates:
        return []

    recovered: list[UUID] = []
    for action_id, organization_id in candidates:
        action = await recover_stale_action(
            db,
            action_id=action_id,
            organization_id=organization_id,
            cutoff=cutoff,
            now=now,
            timeout_minutes=timeout_minutes,
        )
        if action is not None:
            recovered.append(action.id)

    if recovered:
        logger.info(
            "Stale execution recovery completed recovered=%d scanned=%d cutoff=%s",
            len(recovered),
            len(candidates),
            cutoff.isoformat(),
        )
    return recovered


async def count_stale_executing_actions(db: AsyncSession, *, now: datetime | None = None) -> int:
    settings = get_settings()
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(minutes=settings.autonomous_execution_stale_timeout_minutes)
    rows = await db.execute(
        select(AIAction.id).where(
            AIAction.status == AIActionStatus.executing,
            AIAction.executing_at.isnot(None),
            AIAction.executing_at <= cutoff,
        )
    )
    return len(rows.scalars().all())
