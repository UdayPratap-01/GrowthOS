"""Apply provider reconciliation outcomes to ambiguous AI actions."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.automation.idempotency import sanitize_platform_response
from app.jobs.queue import JobQueue
from app.models.automation import AIAction
from app.models.enums import AIActionStatus
from app.publishing.ads_reconciliation import AdsReconciler, ReconciliationResult
from app.publishing.provider_errors import (
    ReconciliationOutcome,
    ReconciliationState,
    reconciliation_blocks_retry,
)
from app.security.audit import write_audit

logger = logging.getLogger(__name__)

PROVIDER_RECONCILE_JOB = "provider.reconcile"


def provider_reconcile_dedupe_key(action_id: UUID) -> str:
    return f"provider-reconcile:{action_id}"


def build_reconciliation_metadata(
    *,
    provider: str,
    operation: str,
    external_id: str | None,
    error_code: str,
    platform: str | None,
) -> dict:
    now = datetime.now(timezone.utc).isoformat()
    return {
        "state": ReconciliationState.pending.value,
        "provider": provider,
        "operation": operation,
        "external_id": external_id,
        "platform": platform,
        "ambiguous_error_code": error_code,
        "ambiguous_since": now,
        "last_checked_at": None,
        "last_outcome": None,
    }


async def enqueue_provider_reconciliation(
    db: AsyncSession,
    *,
    action_id: UUID,
    organization_id: UUID,
):
    return await JobQueue(db).enqueue(
        job_type=PROVIDER_RECONCILE_JOB,
        payload={"action_id": str(action_id)},
        organization_id=organization_id,
        dedupe_key=provider_reconcile_dedupe_key(action_id),
        max_attempts=5,
    )


async def apply_reconciliation_outcome(
    db: AsyncSession,
    *,
    action: AIAction,
    result: ReconciliationResult,
    trigger: str = "reconciliation_job",
) -> AIAction | None:
    """
    Atomically transition an ambiguous FAILED action based on reconciliation.

    Returns the updated action when this caller applied the transition.
    """
    current = await db.scalar(
        select(AIAction).where(
            AIAction.id == action.id,
            AIAction.organization_id == action.organization_id,
            AIAction.status == AIActionStatus.failed,
        )
    )
    if current is None or not reconciliation_blocks_retry(current):
        return None

    now = datetime.now(timezone.utc)
    recon = dict((current.result or {}).get("reconciliation") or {})
    if recon.get("state") not in {
        ReconciliationState.pending.value,
        ReconciliationState.unknown.value,
    }:
        return None

    if result.outcome == ReconciliationOutcome.unsupported:
        recon.update(
            {
                "state": ReconciliationState.unknown.value,
                "last_outcome": result.outcome.value,
                "last_checked_at": now.isoformat(),
                "message": result.message,
            }
        )
        values = {
            "error": f"PROVIDER_STATE_UNKNOWN: {result.message}",
            "result": {**(current.result or {}), "reconciliation": recon},
        }
    elif result.outcome == ReconciliationOutcome.confirmed_success:
        safe_response = sanitize_platform_response(result.platform_response)
        recon.update(
            {
                "state": ReconciliationState.confirmed_success.value,
                "last_outcome": result.outcome.value,
                "last_checked_at": now.isoformat(),
                "observed_state": result.observed_state,
            }
        )
        new_result = {
            **(current.result or {}),
            "reconciliation": recon,
            "confirmed": True,
            "demo": False,
            "status": "reconciled_success",
            "message": result.message,
            "external_id": result.external_id or current.external_id,
            "platform_response": safe_response,
            "observed_state": result.observed_state,
        }
        values = {
            "status": AIActionStatus.completed,
            "error": None,
            "executing_at": None,
            "executed_at": now,
            "external_id": result.external_id or current.external_id,
            "result": new_result,
        }
    elif result.outcome == ReconciliationOutcome.confirmed_not_applied:
        recon.update(
            {
                "state": ReconciliationState.confirmed_not_applied.value,
                "last_outcome": result.outcome.value,
                "last_checked_at": now.isoformat(),
                "observed_state": result.observed_state,
            }
        )
        values = {
            "status": AIActionStatus.failed,
            "error": f"PROVIDER_NOT_APPLIED: {result.message}",
            "executing_at": None,
            "result": {
                **(current.result or {}),
                "reconciliation": recon,
                "confirmed": False,
                "platform_response": sanitize_platform_response(result.platform_response),
            },
        }
    else:  # UNKNOWN
        recon.update(
            {
                "state": ReconciliationState.unknown.value,
                "last_outcome": result.outcome.value,
                "last_checked_at": now.isoformat(),
                "observed_state": result.observed_state,
                "message": result.message,
            }
        )
        values = {
            "error": f"PROVIDER_STATE_UNKNOWN: {result.message}",
            "result": {**(current.result or {}), "reconciliation": recon},
        }

    # CAS: only transition while reconciliation is still open.
    update_result = await db.execute(
        update(AIAction)
        .where(
            AIAction.id == current.id,
            AIAction.organization_id == current.organization_id,
            AIAction.status == AIActionStatus.failed,
        )
        .values(**values)
        .execution_options(synchronize_session=False)
    )
    if update_result.rowcount != 1:
        return None

    await write_audit(
        db,
        action="ai_action.provider_reconciled",
        organization_id=current.organization_id,
        user_id=None,
        resource_type="ai_action",
        resource_id=str(current.id),
        details={
            "trigger": trigger,
            "provider": result.provider,
            "operation": result.operation,
            "reconciliation_result": result.outcome.value,
            "external_id": result.external_id,
            "timestamp": now.isoformat(),
            "previous_status": AIActionStatus.failed.value,
            "new_status": (
                values["status"].value
                if isinstance(values.get("status"), AIActionStatus)
                else AIActionStatus.failed.value
            ),
            "observed_state": result.observed_state,
        },
    )
    await db.flush()
    await db.refresh(current)
    logger.info(
        "Provider reconciliation applied action=%s outcome=%s provider=%s",
        current.id,
        result.outcome.value,
        result.provider,
    )
    return current


async def reconcile_action(
    db: AsyncSession,
    *,
    action_id: UUID,
    organization_id: UUID,
    trigger: str = "reconciliation_job",
) -> dict:
    action = await db.scalar(
        select(AIAction).where(
            AIAction.id == action_id,
            AIAction.organization_id == organization_id,
        )
    )
    if action is None:
        return {"skipped": True, "reason": "NOT_FOUND"}
    if action.status != AIActionStatus.failed:
        return {"skipped": True, "reason": "NOT_AMBIGUOUS", "status": action.status.value}
    if not reconciliation_blocks_retry(action):
        return {"skipped": True, "reason": "NOT_PENDING_RECONCILIATION"}

    campaign = None
    if action.target_id:
        from app.automation.execution import ExecutionEngine

        campaign = await ExecutionEngine(db)._get_campaign(action)

    result = await AdsReconciler(db).reconcile(action, campaign=campaign)
    updated = await apply_reconciliation_outcome(db, action=action, result=result, trigger=trigger)
    if updated is None:
        return {"skipped": True, "reason": "CONCURRENT_RECONCILIATION", "outcome": result.outcome.value}
    return {
        "action_id": str(action_id),
        "outcome": result.outcome.value,
        "status": updated.status.value,
        "reconciliation_state": (updated.result or {}).get("reconciliation", {}).get("state"),
    }
