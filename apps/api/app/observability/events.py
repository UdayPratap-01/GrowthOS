"""
Domain event logging.

Thin wrappers so that the events an operator actually needs — who logged in, what
was denied, which generation failed, which webhook arrived — are logged with
consistent field names instead of ad-hoc strings scattered across services.

Nothing here accepts a secret. Identifiers are logged; credentials, tokens and
payload bodies are not. Emails are hashed for failed logins so the log does not
become a list of valid usernames.
"""

from __future__ import annotations

import hashlib
import logging
from typing import Any

logger = logging.getLogger("growthos.events")


def _hash_email(email: str) -> str:
    return hashlib.sha256(email.strip().lower().encode()).hexdigest()[:16]


# --------------------------------------------------------------------------
# Authentication and authorization
# --------------------------------------------------------------------------


def auth_success(*, user_id: Any, organization_id: Any = None, method: str = "password") -> None:
    logger.info(
        "Authentication succeeded",
        extra={
            "event": "auth.success",
            "auth_user_id": str(user_id),
            "org": str(organization_id) if organization_id else None,
            "auth_method": method,
        },
    )


def auth_failure(*, email: str, reason: str) -> None:
    """
    The email is hashed on purpose.

    Failed-login logs are the one place where a plaintext address is an
    enumeration list for anyone who reads the logs. The hash is still stable
    enough to correlate repeated attempts against one account.
    """
    logger.warning(
        "Authentication failed",
        extra={"event": "auth.failure", "email_hash": _hash_email(email), "reason": reason},
    )


def authorization_denied(*, user_id: Any, organization_id: Any, permission: str, role: str) -> None:
    logger.warning(
        "Authorization denied",
        extra={
            "event": "authz.denied",
            "auth_user_id": str(user_id),
            "org": str(organization_id),
            "permission": permission,
            "role": role,
        },
    )


# --------------------------------------------------------------------------
# AI and media
# --------------------------------------------------------------------------


def ai_generation(
    *, provider: str, operation: str, organization_id: Any = None, success: bool, detail: str | None = None
) -> None:
    logger.log(
        logging.INFO if success else logging.ERROR,
        "AI generation %s", "succeeded" if success else "failed",
        extra={
            "event": "ai.generation",
            "provider": provider,
            "operation": operation,
            "org": str(organization_id) if organization_id else None,
            "success": success,
            "detail": detail,
        },
    )


def media_generation(
    *,
    kind: str,
    provider: str,
    job_id: Any,
    organization_id: Any = None,
    status: str,
    error: str | None = None,
) -> None:
    logger.log(
        logging.ERROR if status.upper() == "FAILED" else logging.INFO,
        "Media generation %s: %s",
        kind,
        status,
        extra={
            "event": "media.generation",
            "media_kind": kind,
            "provider": provider,
            "media_job_id": str(job_id),
            "org": str(organization_id) if organization_id else None,
            "job_status": status,
            "error_detail": error,
        },
    )


def storage_error(*, operation: str, key: str, detail: str) -> None:
    logger.error(
        "Storage %s failed",
        operation,
        extra={
            "event": "storage.error",
            "operation": operation,
            "storage_key": key,
            "detail": detail,
        },
    )


# --------------------------------------------------------------------------
# Integrations, webhooks, campaigns
# --------------------------------------------------------------------------


def integration_sync(
    *, provider: str, organization_id: Any, success: bool, records: int | None = None, message: str | None = None
) -> None:
    logger.log(
        logging.INFO if success else logging.WARNING,
        "Integration sync %s for %s",
        "succeeded" if success else "failed",
        provider,
        extra={
            "event": "integration.sync",
            "provider": provider,
            "org": str(organization_id),
            "success": success,
            "records": records,
            "detail": message,
        },
    )


def webhook_received(*, provider: str, event_id: str | None, outcome: str, detail: str | None = None) -> None:
    logger.info(
        "Webhook %s from %s",
        outcome,
        provider,
        extra={
            "event": "webhook.received",
            "provider": provider,
            "webhook_event_id": event_id,
            "outcome": outcome,
            "detail": detail,
        },
    )


def campaign_execution(
    *, action: str, organization_id: Any, success: bool, external_id: str | None = None, detail: str | None = None
) -> None:
    """Financially significant. Logged at WARNING when it did not succeed."""
    logger.log(
        logging.INFO if success else logging.WARNING,
        "Campaign execution %s: %s",
        action,
        "succeeded" if success else "failed",
        extra={
            "event": "campaign.execution",
            "action": action,
            "org": str(organization_id),
            "success": success,
            "external_id": external_id,
            "detail": detail,
        },
    )


def database_error(*, operation: str, detail: str) -> None:
    logger.error(
        "Database error during %s",
        operation,
        extra={"event": "database.error", "operation": operation, "detail": detail},
    )


def auth_refresh_rejected(*, reason: str) -> None:
    """The reason is logged; the caller is told nothing beyond 401."""
    logger.warning(
        "Refresh token rejected",
        extra={"event": "auth.refresh_rejected", "reason": reason},
    )


def auth_sessions_revoked(*, user_id: Any, count: int) -> None:
    logger.info(
        "Sessions revoked",
        extra={
            "event": "auth.sessions_revoked",
            "auth_user_id": str(user_id),
            "revoked": count,
        },
    )


# --------------------------------------------------------------------------
# Autonomous optimization / production safety (Milestone 4)
# --------------------------------------------------------------------------


def autonomous_gate_blocked(
    *, organization_id: Any, code: str, intent: str, detail: str | None = None
) -> None:
    logger.warning(
        "Autonomous gate blocked: %s",
        code,
        extra={
            "event": "autonomous.gate_blocked",
            "org": str(organization_id),
            "code": code,
            "intent": intent,
            "detail": (detail or "")[:300],
        },
    )


def optimization_cycle(
    *,
    organization_id: Any,
    evaluated: int,
    created: int,
    blocked: int,
    approval_required: int,
) -> None:
    logger.info(
        "Optimization cycle org=%s evaluated=%s created=%s blocked=%s approval=%s",
        organization_id,
        evaluated,
        created,
        blocked,
        approval_required,
        extra={
            "event": "optimization.cycle",
            "org": str(organization_id),
            "evaluated": evaluated,
            "actions_created": created,
            "blocked": blocked,
            "approval_required": approval_required,
        },
    )


def reconciliation_metric(
    *, organization_id: Any, outcome: str, trigger: str = "job"
) -> None:
    logger.info(
        "Reconciliation outcome=%s",
        outcome,
        extra={
            "event": "reconciliation.outcome",
            "org": str(organization_id),
            "outcome": outcome,
            "trigger": trigger,
        },
    )


def stale_recovery_metric(*, organization_id: Any | None, recovered: int) -> None:
    logger.info(
        "Stale recovery recovered=%s",
        recovered,
        extra={
            "event": "stale_recovery.batch",
            "org": str(organization_id) if organization_id else None,
            "recovered": recovered,
        },
    )
