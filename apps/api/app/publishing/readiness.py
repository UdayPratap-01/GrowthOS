"""Operator-facing provider readiness stages (no secrets, no live network).

These labels are for UX and runbooks. They never enable mutations.
Verified ≠ autonomous spend. Canary ready ≠ unrestricted autonomy.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


# Operator UX vocabulary (Phase 10).
STAGE_NOT_CONFIGURED = "NOT_CONFIGURED"
STAGE_NOT_CONNECTED = "NOT_CONNECTED"
STAGE_CONNECTED = "CONNECTED"
STAGE_VERIFICATION_REQUIRED = "VERIFICATION_REQUIRED"
STAGE_VERIFIED = "VERIFIED"
STAGE_VERIFICATION_FAILED = "VERIFICATION_FAILED"
STAGE_CANARY_READY = "CANARY_READY"
STAGE_CANARY_BLOCKED = "CANARY_BLOCKED"
STAGE_DEMO = "DEMO"


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def verification_age_hours(checked_at: str | None) -> float | None:
    dt = _parse_iso(checked_at)
    if not dt:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return max(0.0, (datetime.now(timezone.utc) - dt).total_seconds() / 3600.0)


def derive_provider_readiness_stage(
    *,
    credentials_configured: bool,
    integration_connected: bool,
    demo_mode: bool = False,
    last_verification: dict[str, Any] | None = None,
    canary_provider_ready: bool | None = None,
    canary_enabled: bool = False,
    kill_switch: bool = False,
    max_age_hours: float = 24.0,
) -> dict[str, Any]:
    """
    Derive a single operator stage for Meta or Google Ads.

    `canary_provider_ready` is True when server canary status lists this provider
    as connected with a fresh VERIFIED snapshot and canary gates are otherwise OK
    for the org — or None when unknown (omit canary stages).
    """
    last = last_verification or {}
    verify_status = str(last.get("status") or "").upper()
    checked_at = last.get("checked_at")
    age = verification_age_hours(str(checked_at) if checked_at else None)
    stale = age is not None and age > max_age_hours
    failure_reason = (
        last.get("error_category")
        or last.get("skipped_reason")
        or _first_failed_step_detail(last)
    )
    live_ran = last.get("live_provider_verification") == "RAN" or bool(last.get("ran"))

    stage = STAGE_NOT_CONFIGURED
    if demo_mode and not credentials_configured:
        stage = STAGE_DEMO
    elif not credentials_configured:
        stage = STAGE_NOT_CONFIGURED
    elif not integration_connected:
        stage = STAGE_NOT_CONNECTED
    elif verify_status == "VERIFIED" and not stale:
        stage = STAGE_VERIFIED
    elif verify_status in {"VERIFICATION_FAILED", "FAILED"} or (
        live_ran and verify_status and verify_status != "VERIFIED"
    ):
        stage = STAGE_VERIFICATION_FAILED
    elif integration_connected:
        stage = STAGE_VERIFICATION_REQUIRED if not (verify_status == "VERIFIED" and not stale) else STAGE_VERIFIED
        if verify_status == "VERIFIED" and stale:
            stage = STAGE_VERIFICATION_REQUIRED
        elif not verify_status or verify_status in {"NOT_CONFIGURED", "NOT_CONNECTED", "BLOCKED", "DEMO"}:
            stage = STAGE_CONNECTED if not live_ran else STAGE_VERIFICATION_REQUIRED
    else:
        stage = STAGE_NOT_CONNECTED

    # Canary overlay — never upgrades past verified without canary gates.
    if stage == STAGE_VERIFIED and canary_provider_ready is not None:
        if kill_switch or not canary_enabled:
            stage = STAGE_CANARY_BLOCKED
        elif canary_provider_ready:
            stage = STAGE_CANARY_READY
        else:
            stage = STAGE_CANARY_BLOCKED

    return {
        "stage": stage,
        "credentials_configured": credentials_configured,
        "connected": integration_connected,
        "verification_status": verify_status or None,
        "verification_checked_at": checked_at,
        "verification_age_hours": round(age, 2) if age is not None else None,
        "verification_stale": stale,
        "failure_reason": failure_reason,
        "live_provider_verification": last.get("live_provider_verification")
        or ("RAN" if live_ran else "NOT_RUN"),
        "account": last.get("account") or None,
        "campaigns": ((last.get("canary_resources") or {}).get("campaigns") or []),
        "note": (
            "Verified ≠ autonomous spend. Canary ready ≠ unrestricted autonomy. "
            "Live mutations stay OFF until explicit canary allowlists."
        ),
    }


def _first_failed_step_detail(last: dict[str, Any]) -> str | None:
    for step in last.get("steps") or last.get("checks") or []:
        if isinstance(step, dict) and step.get("ok") is False:
            return str(step.get("detail") or step.get("reason") or step.get("step") or "verification step failed")
        if isinstance(step, dict) and str(step.get("status") or "").upper() == "FAIL":
            return str(step.get("detail") or step.get("reason") or step.get("name") or "check failed")
    return None


def production_readiness_summary(
    *,
    meta_credentials: bool,
    google_credentials: bool,
    meta_stage: str,
    google_stage: str,
    autonomous_execution_enabled: bool,
    canary_enabled: bool,
    optimization_enabled: bool,
    kill_switch: bool,
    demo_mode: bool,
) -> dict[str, Any]:
    """Honest product/platform status for operator banners (never implies live verified)."""
    code_ready = True
    meta_verified = meta_stage in {STAGE_VERIFIED, STAGE_CANARY_READY, STAGE_CANARY_BLOCKED}
    google_verified = google_stage in {STAGE_VERIFIED, STAGE_CANARY_READY, STAGE_CANARY_BLOCKED}
    provider_pending = not (meta_verified and google_verified)

    return {
        "code_ready": code_ready,
        "real_provider_verification_pending": provider_pending or not (meta_credentials or google_credentials),
        "production_deployment_pending": True,  # hosting is always operator-owned until drill evidence
        "demo_mode": demo_mode,
        "live_mutations_default_off": not (
            autonomous_execution_enabled or canary_enabled or optimization_enabled
        ),
        "latches": {
            "autonomous_execution_enabled": autonomous_execution_enabled,
            "optimization_enabled": optimization_enabled,
            "canary_enabled": canary_enabled,
            "autonomous_kill_switch": kill_switch,
            "meta_autonomous": False,  # filled by caller when needed
            "google_autonomous": False,
        },
        "meta": {
            "credentials_configured": meta_credentials,
            "stage": meta_stage,
            "live_verification": "COMPLETE" if meta_verified else "PENDING",
        },
        "google": {
            "credentials_configured": google_credentials,
            "stage": google_stage,
            "live_verification": "COMPLETE" if google_verified else "PENDING",
        },
        "banners": {
            "code": "CODE READY",
            "providers": (
                "REAL PROVIDER VERIFICATION PENDING"
                if provider_pending or not (meta_credentials and google_credentials)
                else "PROVIDER VERIFICATION RECORDED (confirm canary before launch)"
            ),
            "deployment": "PRODUCTION DEPLOYMENT PENDING",
        },
        "never_enable_before_launch": [
            "AUTONOMOUS_EXECUTION_ENABLED=true without completed Meta/Google canaries",
            "META_AUTONOMOUS_ENABLED=true / GOOGLE_AUTONOMOUS_ENABLED=true without allowlists",
            "OPTIMIZATION_ENABLED=true with autonomous mutations before controlled verification",
            "CANARY_ENABLED=true against production ad accounts without test allowlists",
            "DEMO_MODE=true in ENVIRONMENT=production",
            "AI_PROVIDER=mock in ENVIRONMENT=production",
        ],
    }
