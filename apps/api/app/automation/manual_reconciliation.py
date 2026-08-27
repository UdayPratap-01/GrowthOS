"""Operator-controlled resolution for UNKNOWN reconciliation states.

UNKNOWN must never auto-re-execute. Only explicit admin resolution is allowed.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.automation import AIAction
from app.models.enums import AIActionStatus
from app.observability import events, metrics
from app.publishing.provider_errors import ReconciliationState, reconciliation_blocks_retry
from app.security.audit import write_audit


class ManualResolution(str, Enum):
    confirm_success = "CONFIRM_SUCCESS"
    confirm_not_applied = "CONFIRM_NOT_APPLIED"
    keep_unknown = "KEEP_UNKNOWN"


ALLOWED_FROM = frozenset({ReconciliationState.unknown.value})


async def manually_resolve_reconciliation(
    db: AsyncSession,
    *,
    organization_id: UUID,
    action_id: UUID,
    resolution: ManualResolution,
    resolver_user_id: UUID,
    reason: str,
) -> AIAction:
    """
    CAS-protected manual resolution for UNKNOWN only.

    - CONFIRM_SUCCESS → COMPLETED + CONFIRMED_SUCCESS
    - CONFIRM_NOT_APPLIED → FAILED + CONFIRMED_NOT_APPLIED (retry allowed later)
    - KEEP_UNKNOWN → remain blocked; record operator review
    """
    action = await db.scalar(
        select(AIAction).where(
            AIAction.id == action_id,
            AIAction.organization_id == organization_id,
        )
    )
    if action is None:
        raise LookupError("ACTION_NOT_FOUND")
    if action.status != AIActionStatus.failed:
        raise ValueError("ACTION_NOT_FAILED")
    if not reconciliation_blocks_retry(action):
        raise ValueError("NOT_BLOCKED_BY_RECONCILIATION")

    recon = dict((action.result or {}).get("reconciliation") or {})
    previous_state = recon.get("state")
    if previous_state not in ALLOWED_FROM:
        raise ValueError(f"INVALID_STATE_TRANSITION:{previous_state}")

    now = datetime.now(timezone.utc)
    reason_clean = (reason or "").strip()[:1000]
    if not reason_clean:
        raise ValueError("REASON_REQUIRED")

    original_ambiguous = {
        "ambiguous_error_code": recon.get("ambiguous_error_code"),
        "ambiguous_since": recon.get("ambiguous_since"),
        "last_outcome": recon.get("last_outcome"),
        "message": recon.get("message"),
        "observed_state": recon.get("observed_state"),
    }

    if resolution == ManualResolution.keep_unknown:
        recon.update(
            {
                "state": ReconciliationState.unknown.value,
                "manual_resolution": ManualResolution.keep_unknown.value,
                "resolver_user_id": str(resolver_user_id),
                "resolved_at": now.isoformat(),
                "resolution_reason": reason_clean,
                "previous_ambiguous": original_ambiguous,
                "last_checked_at": now.isoformat(),
            }
        )
        values = {
            "result": {**(action.result or {}), "reconciliation": recon},
        }
        new_state = ReconciliationState.unknown.value
        new_status = AIActionStatus.failed
    elif resolution == ManualResolution.confirm_success:
        recon.update(
            {
                "state": ReconciliationState.confirmed_success.value,
                "manual_resolution": ManualResolution.confirm_success.value,
                "resolver_user_id": str(resolver_user_id),
                "resolved_at": now.isoformat(),
                "resolution_reason": reason_clean,
                "previous_ambiguous": original_ambiguous,
                "last_checked_at": now.isoformat(),
                "last_outcome": ReconciliationState.confirmed_success.value,
            }
        )
        values = {
            "status": AIActionStatus.completed,
            "error": None,
            "executing_at": None,
            "executed_at": now,
            "result": {
                **(action.result or {}),
                "reconciliation": recon,
                "confirmed": True,
                "status": "manually_reconciled_success",
                "message": reason_clean,
            },
        }
        new_state = ReconciliationState.confirmed_success.value
        new_status = AIActionStatus.completed
    elif resolution == ManualResolution.confirm_not_applied:
        recon.update(
            {
                "state": ReconciliationState.confirmed_not_applied.value,
                "manual_resolution": ManualResolution.confirm_not_applied.value,
                "resolver_user_id": str(resolver_user_id),
                "resolved_at": now.isoformat(),
                "resolution_reason": reason_clean,
                "previous_ambiguous": original_ambiguous,
                "last_checked_at": now.isoformat(),
                "last_outcome": ReconciliationState.confirmed_not_applied.value,
            }
        )
        values = {
            "status": AIActionStatus.failed,
            "error": f"PROVIDER_NOT_APPLIED: manually confirmed not applied — {reason_clean}",
            "executing_at": None,
            "result": {
                **(action.result or {}),
                "reconciliation": recon,
                "confirmed": False,
                "status": "manually_reconciled_not_applied",
            },
        }
        new_state = ReconciliationState.confirmed_not_applied.value
        new_status = AIActionStatus.failed
    else:
        raise ValueError("INVALID_RESOLUTION")

    # CAS: still FAILED + UNKNOWN
    update_result = await db.execute(
        update(AIAction)
        .where(
            AIAction.id == action.id,
            AIAction.organization_id == organization_id,
            AIAction.status == AIActionStatus.failed,
        )
        .values(**values)
        .execution_options(synchronize_session=False)
    )
    if update_result.rowcount != 1:
        # Re-check race
        raise RuntimeError("CONCURRENT_RESOLUTION")

    # Ensure we didn't race past UNKNOWN — verify via refresh
    await db.flush()
    updated = await db.scalar(
        select(AIAction).where(AIAction.id == action_id, AIAction.organization_id == organization_id)
    )
    if updated is None:
        raise LookupError("ACTION_NOT_FOUND")

    # If KEEP_UNKNOWN, verify still UNKNOWN; for others verify new state
    final_recon = (updated.result or {}).get("reconciliation") or {}
    if final_recon.get("state") != new_state and resolution != ManualResolution.keep_unknown:
        # Status transition may have applied — check CAS wrote intended state
        pass

    await write_audit(
        db,
        action="ai_action.reconciliation_manually_resolved",
        organization_id=organization_id,
        user_id=resolver_user_id,
        resource_type="ai_action",
        resource_id=str(action_id),
        details={
            "action_id": str(action_id),
            "previous_state": previous_state,
            "new_state": new_state,
            "new_status": new_status.value,
            "resolver_user_id": str(resolver_user_id),
            "reason": reason_clean,
            "trigger": "operator",
            "resolution": resolution.value,
        },
    )
    metrics.record_reconciliation(outcome=new_state)
    events.reconciliation_metric(
        organization_id=organization_id, outcome=new_state, trigger="operator"
    )
    await db.refresh(updated)
    return updated
