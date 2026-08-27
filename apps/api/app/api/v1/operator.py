"""Operator control APIs — status, ambiguous actions, legacy recovery, manual resolve."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.automation.legacy_executing import (
    LegacyRecoveryAction,
    list_legacy_executing,
    recover_legacy_executing,
)
from app.automation.manual_reconciliation import ManualResolution, manually_resolve_reconciliation
from app.automation.production_gates import normalize_provider
from app.core.config import get_settings as app_settings
from app.core.deps import AuthContext, get_current_auth
from app.core.permissions import Permission, require_permission
from app.db.session import get_db
from app.integrations.persistence import get_integration_row
from app.models.ai_ops import AuditLog
from app.models.automation import ActionExecution, AIAction
from app.models.enums import AIActionStatus
from app.optimization.modes import resolve_optimization_mode
from app.publishing.provider_errors import ReconciliationState
from app.schemas.autopilot import AIActionOut
from app.services.action_service import ActionService
from app.services.autonomy_service import AutonomyService

router = APIRouter(prefix="/autopilot/operator", tags=["operator"])


class ManualResolveBody(BaseModel):
    resolution: ManualResolution
    reason: str = Field(min_length=3, max_length=1000)


class LegacyRecoverBody(BaseModel):
    recovery: LegacyRecoveryAction
    reason: str = Field(min_length=3, max_length=1000)


class ProviderVerifyBody(BaseModel):
    confirm: str = Field(
        ...,
        description="Must equal I_CONFIRM_READ_ONLY_PROVIDER_VERIFICATION — read-only, no ad mutations.",
    )
    client_id: UUID | None = None


class ProviderPreflightBody(BaseModel):
    client_id: UUID | None = None


def _recon_state(action: AIAction) -> str | None:
    return ((action.result or {}).get("reconciliation") or {}).get("state")


def _normalize_provider_param(provider: str) -> str:
    p = (provider or "").strip().lower()
    if p in {"meta", "facebook"}:
        return "meta"
    if p in {"google", "google_ads"}:
        return "google_ads"
    raise HTTPException(status_code=400, detail="provider must be meta or google_ads")


@router.get("/providers")
async def list_provider_preflight(
    client_id: UUID | None = Query(default=None),
    auth: AuthContext = Depends(get_current_auth),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Sanitized preflight + last verification for Meta and Google Ads."""
    from app.publishing.provider_preflight import run_provider_preflight

    items = []
    for provider in ("meta", "google_ads"):
        pre = await run_provider_preflight(
            db, organization_id=auth.organization_id, provider=provider, client_id=client_id
        )
        row = await get_integration_row(
            db, organization_id=auth.organization_id, provider=provider, client_id=client_id
        )
        if (not row) and client_id is not None:
            row = await get_integration_row(
                db, organization_id=auth.organization_id, provider=provider, client_id=None
            )
        last = (row.config or {}).get("last_verification") if row else None
        items.append(
            {
                **pre.as_dict(),
                "last_verification": last,
                "mutation": "DISABLED_IN_PHASE_1",
            }
        )
    return {
        "items": items,
        "notes": [
            "PROVIDER VERIFIED does not mean autonomous spend is enabled.",
            "Phase 1 verification is read-only — no campaigns, ads, budgets, or spend are changed.",
            "Confirm phrase for live verify: I_CONFIRM_READ_ONLY_PROVIDER_VERIFICATION",
        ],
    }


@router.post("/providers/{provider}/preflight")
async def provider_preflight(
    provider: str,
    body: ProviderPreflightBody | None = None,
    auth: AuthContext = Depends(get_current_auth),
    db: AsyncSession = Depends(get_db),
) -> dict:
    from app.publishing.provider_preflight import run_provider_preflight
    from app.security.audit import write_audit

    provider = _normalize_provider_param(provider)
    client_id = body.client_id if body else None
    await write_audit(
        db,
        action="provider.preflight_started",
        organization_id=auth.organization_id,
        user_id=auth.user.id,
        resource_type="provider",
        resource_id=provider,
        details={"provider": provider, "trigger": "operator"},
    )
    result = await run_provider_preflight(
        db, organization_id=auth.organization_id, provider=provider, client_id=client_id
    )
    await write_audit(
        db,
        action="provider.preflight_completed",
        organization_id=auth.organization_id,
        user_id=auth.user.id,
        resource_type="provider",
        resource_id=provider,
        details={"provider": provider, "status": result.status.value},
    )
    await db.commit()
    return result.as_dict()


@router.post("/providers/{provider}/verify")
async def provider_verify_readonly(
    provider: str,
    body: ProviderVerifyBody,
    auth: AuthContext = Depends(require_permission(Permission.integration_connect)),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """
    Explicit read-only verification against Meta/Google when connected.

    Never mutates ads. Never creates AIAction. Requires confirmation phrase.
    """
    from app.publishing.provider_verification import verify_provider_readonly

    provider = _normalize_provider_param(provider)
    report = await verify_provider_readonly(
        db,
        organization_id=auth.organization_id,
        provider=provider,
        client_id=body.client_id,
        confirm=body.confirm,
        actor_user_id=auth.user.id,
    )
    await db.commit()
    return report.as_dict()


@router.get("/providers/{provider}/verification")
async def get_provider_verification(
    provider: str,
    client_id: UUID | None = Query(default=None),
    auth: AuthContext = Depends(get_current_auth),
    db: AsyncSession = Depends(get_db),
) -> dict:
    provider = _normalize_provider_param(provider)
    row = await get_integration_row(
        db, organization_id=auth.organization_id, provider=provider, client_id=client_id
    )
    if (not row) and client_id is not None:
        row = await get_integration_row(
            db, organization_id=auth.organization_id, provider=provider, client_id=None
        )
    last = (row.config or {}).get("last_verification") if row else None
    return {
        "provider": provider,
        "last_verification": last,
        "safe_for_mutation": False,
        "mutation": "DISABLED_IN_PHASE_1",
    }


@router.get("/status")
async def operator_status(
    client_id: UUID | None = Query(default=None),
    auth: AuthContext = Depends(get_current_auth),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Operator control-center snapshot — no secrets."""
    cfg = app_settings()
    settings = await AutonomyService(db).get_effective(auth.organization_id, client_id)
    mode = resolve_optimization_mode(settings)

    providers = {}
    for provider in ("meta", "google_ads"):
        row = await get_integration_row(
            db, organization_id=auth.organization_id, provider=provider, client_id=client_id
        )
        if (not row or not row.secret_ref) and client_id is not None:
            row = await get_integration_row(
                db, organization_id=auth.organization_id, provider=provider, client_id=None
            )
        connected = bool(row and row.secret_ref and (row.status or "").lower() in {"connected", "active", ""})
        if provider == "meta":
            configured = bool(cfg.meta_app_id and cfg.meta_app_secret)
            autonomous_flag = cfg.meta_autonomous_enabled
        else:
            configured = bool(
                cfg.google_client_id and cfg.google_client_secret and cfg.google_ads_developer_token
            )
            autonomous_flag = cfg.google_autonomous_enabled
        providers[provider] = {
            "connected": connected,
            "credentials_configured": configured,
            "autonomous_enabled": autonomous_flag,
            "status": (
                "HEALTHY"
                if connected and configured
                else ("NOT_CONFIGURED" if not configured else ("DEGRADED" if not connected else "DISABLED"))
            ),
        }

    from sqlalchemy import func

    since = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    daily_actions = int(
        await db.scalar(
            select(func.count()).select_from(AIAction).where(
                AIAction.organization_id == auth.organization_id,
                AIAction.agent == "closed_loop_optimizer",
                AIAction.created_at >= since,
            )
        )
        or 0
    )

    return {
        "optimization_enabled": cfg.optimization_enabled,
        "autonomous_execution_enabled": cfg.autonomous_execution_enabled,
        "autonomous_kill_switch": cfg.autonomous_kill_switch,
        "optimization_mode": mode.value,
        "autonomy_mode": settings.autonomy_mode.value,
        "automation_enabled": settings.automation_enabled,
        "providers": providers,
        "safety": {
            "max_budget_increase_pct": float(settings.maximum_budget_increase_percentage or 0),
            "max_budget_decrease_pct": float(settings.maximum_budget_decrease_percentage or 0),
            "maximum_campaign_budget": float(settings.maximum_campaign_budget or 0),
            "maximum_daily_ad_spend": float(settings.maximum_daily_ad_spend or 0),
            "allowed_actions": settings.allowed_actions or [],
            "cooldown_hours": cfg.optimization_cooldown_hours,
            "opposite_cooldown_hours": cfg.optimization_opposite_cooldown_hours,
            "max_actions_per_day": cfg.optimization_max_actions_per_day,
            "max_autonomous_risk": cfg.optimization_max_autonomous_risk,
            "canary_orgs_configured": bool((cfg.autonomous_canary_org_ids or "").strip()),
            "canary_providers": cfg.autonomous_canary_providers,
            "canary_action_types": cfg.autonomous_canary_action_types,
            "max_daily_spend_impact": cfg.autonomous_max_daily_spend_impact,
            "max_campaigns_per_cycle": cfg.autonomous_max_campaigns_per_cycle,
        },
        "usage": {
            "closed_loop_actions_today": daily_actions,
            "max_actions_per_day": cfg.optimization_max_actions_per_day,
        },
        "kill_switch": {
            "enabled": cfg.autonomous_kill_switch,
            "effect": "Blocks NEW autonomous mutations; analysis and approvals still allowed",
        },
        "scheduler_enabled": cfg.autopilot_scheduler_enabled,
        "provider_verification_enabled": cfg.provider_verification_enabled,
        "canary": {
            "enabled": cfg.canary_enabled,
            "org_allowlisted": (
                auth.organization_id
                in {
                    UUID(p.strip())
                    for p in (cfg.canary_allowed_org_ids or "").split(",")
                    if p.strip()
                }
                if (cfg.canary_allowed_org_ids or "").strip()
                else False
            ),
            "actions": cfg.canary_allowed_actions,
            "providers": cfg.canary_allowed_providers,
            "verification_max_age_hours": cfg.provider_verification_max_age_hours,
            "note": "Canary success ≠ unrestricted production autonomy",
        },
    }


@router.get("/actions/ambiguous")
async def list_ambiguous_actions(
    limit: int = Query(default=50, ge=1, le=200),
    auth: AuthContext = Depends(get_current_auth),
    db: AsyncSession = Depends(get_db),
) -> dict:
    rows = list(
        (
            await db.scalars(
                select(AIAction)
                .where(
                    AIAction.organization_id == auth.organization_id,
                    AIAction.status == AIActionStatus.failed,
                )
                .order_by(AIAction.updated_at.desc())
                .limit(limit * 3)
            )
        ).all()
    )
    items = []
    for action in rows:
        recon = (action.result or {}).get("reconciliation") or {}
        state = recon.get("state")
        if state not in {
            ReconciliationState.pending.value,
            ReconciliationState.unknown.value,
            ReconciliationState.confirmed_success.value,
            ReconciliationState.confirmed_not_applied.value,
        }:
            continue
        items.append(
            {
                "action_id": str(action.id),
                "action_type": action.action_type.value,
                "platform": action.platform,
                "provider": recon.get("provider") or normalize_provider(action.platform),
                "operation": recon.get("operation") or action.action_type.value,
                "external_id": recon.get("external_id") or action.external_id,
                "status": action.status.value,
                "reconciliation_state": state,
                "ambiguous_error": recon.get("ambiguous_error_code") or action.error,
                "ambiguous_since": recon.get("ambiguous_since"),
                "last_checked_at": recon.get("last_checked_at"),
                "last_outcome": recon.get("last_outcome"),
                "message": recon.get("message"),
                "updated_at": action.updated_at.isoformat() if action.updated_at else None,
            }
        )
        if len(items) >= limit:
            break
    return {"items": items, "total": len(items)}


@router.get("/actions/stale-recoveries")
async def list_stale_recoveries(
    limit: int = Query(default=50, ge=1, le=200),
    auth: AuthContext = Depends(get_current_auth),
    db: AsyncSession = Depends(get_db),
) -> dict:
    rows = list(
        (
            await db.scalars(
                select(AuditLog)
                .where(
                    AuditLog.organization_id == auth.organization_id,
                    AuditLog.action.in_(
                        ["ai_action.stale_recovery", "legacy_action.recovered"]
                    ),
                )
                .order_by(AuditLog.created_at.desc())
                .limit(limit)
            )
        ).all()
    )
    return {
        "items": [
            {
                "id": str(row.id),
                "action": row.action,
                "resource_id": row.resource_id,
                "details": row.details or {},
                "created_at": row.created_at.isoformat() if row.created_at else None,
                "user_id": str(row.user_id) if row.user_id else None,
            }
            for row in rows
        ],
        "total": len(rows),
    }


@router.get("/actions/legacy-executing")
async def list_legacy_executing_actions(
    limit: int = Query(default=50, ge=1, le=200),
    auth: AuthContext = Depends(require_permission(Permission.action_execute)),
    db: AsyncSession = Depends(get_db),
) -> dict:
    items = await list_legacy_executing(db, organization_id=auth.organization_id, limit=limit)
    return {"items": items, "total": len(items)}


@router.get("/actions/{action_id}/detail")
async def action_detail(
    action_id: UUID,
    auth: AuthContext = Depends(get_current_auth),
    db: AsyncSession = Depends(get_db),
) -> dict:
    action = await ActionService(db).get(auth.organization_id, action_id)
    executions = list(
        (
            await db.scalars(
                select(ActionExecution)
                .where(
                    ActionExecution.organization_id == auth.organization_id,
                    ActionExecution.action_id == action_id,
                )
                .order_by(ActionExecution.created_at.asc())
            )
        ).all()
    )
    audits = list(
        (
            await db.scalars(
                select(AuditLog)
                .where(
                    AuditLog.organization_id == auth.organization_id,
                    AuditLog.resource_type.in_(["ai_action", "performance_recommendation"]),
                    AuditLog.resource_id == str(action_id),
                )
                .order_by(AuditLog.created_at.asc())
                .limit(100)
            )
        ).all()
    )
    # Also pull recommendation-linked audits from payload
    rec_id = (action.payload or {}).get("recommendation_id") or (
        (action.result or {}).get("recommendation_id")
    )
    payload = action.payload or {}
    opt = payload.get("optimization") or payload.get("closed_loop")
    policy_checks = payload.get("policy_checks") or []
    last_decision = payload.get("last_decision") or {}

    recon = (action.result or {}).get("reconciliation") or {}
    return {
        "action": AIActionOut.model_validate(action).model_dump(mode="json"),
        "lifecycle": {
            "recommendation_id": rec_id,
            "closed_loop": bool(opt),
            "decision": last_decision.get("decision"),
            "autonomy_mode": last_decision.get("autonomy_mode") or payload.get("autonomy_mode"),
            "risk": (action.risk_level.value if action.risk_level else None),
            "policy_checks": policy_checks or last_decision.get("policy_checks") or [],
            "reconciliation": {
                "state": recon.get("state"),
                "last_outcome": recon.get("last_outcome"),
                "last_checked_at": recon.get("last_checked_at"),
                "external_id": recon.get("external_id"),
                "ambiguous_error_code": recon.get("ambiguous_error_code"),
                # never include tokens
            },
        },
        "executions": [
            {
                "id": str(ex.id),
                "status": ex.status.value if ex.status else None,
                "started_at": ex.started_at.isoformat() if ex.started_at else None,
                "finished_at": ex.finished_at.isoformat() if ex.finished_at else None,
                "error_code": ex.error_code,
                "error_message": ex.error_message,
                "is_demo": ex.is_demo,
            }
            for ex in executions
        ],
        "audit_events": [
            {
                "id": str(a.id),
                "action": a.action,
                "created_at": a.created_at.isoformat() if a.created_at else None,
                "user_id": str(a.user_id) if a.user_id else None,
                "details": a.details or {},
            }
            for a in audits
        ],
    }


@router.post("/actions/{action_id}/resolve-reconciliation")
async def resolve_reconciliation(
    action_id: UUID,
    body: ManualResolveBody,
    auth: AuthContext = Depends(require_permission(Permission.action_execute)),
    db: AsyncSession = Depends(get_db),
) -> dict:
    try:
        updated = await manually_resolve_reconciliation(
            db,
            organization_id=auth.organization_id,
            action_id=action_id,
            resolution=body.resolution,
            resolver_user_id=auth.user.id,
            reason=body.reason,
        )
    except LookupError:
        raise HTTPException(status_code=404, detail="Action not found") from None
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None
    await db.commit()
    recon = (updated.result or {}).get("reconciliation") or {}
    return {
        "action_id": str(updated.id),
        "status": updated.status.value,
        "reconciliation_state": recon.get("state"),
        "resolution": body.resolution.value,
    }


@router.post("/actions/{action_id}/legacy-recover")
async def legacy_recover(
    action_id: UUID,
    body: LegacyRecoverBody,
    auth: AuthContext = Depends(require_permission(Permission.action_execute)),
    db: AsyncSession = Depends(get_db),
) -> dict:
    try:
        updated = await recover_legacy_executing(
            db,
            organization_id=auth.organization_id,
            action_id=action_id,
            recovery=body.recovery,
            actor_user_id=auth.user.id,
            reason=body.reason,
        )
    except LookupError:
        raise HTTPException(status_code=404, detail="Action not found") from None
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None
    await db.commit()
    return {
        "action_id": str(updated.id),
        "status": updated.status.value,
        "recovery": body.recovery.value,
        "reconciliation_state": _recon_state(updated),
    }


# ---- Live canary (M5 Phase 2) -------------------------------------------------


class CanaryDryRunBody(BaseModel):
    provider: str
    action_type: str = Field(..., description="pause_campaign | resume_campaign")
    campaign_id: UUID | None = None
    external_campaign_id: str | None = None
    client_id: UUID | None = None


class CanaryExecuteBody(BaseModel):
    provider: str
    action_type: str
    confirm: str = Field(
        ...,
        description="Must equal I_CONFIRM_CANARY_LIVE_PROVIDER_EXECUTION",
    )
    campaign_id: UUID | None = None
    external_campaign_id: str | None = None
    client_id: UUID | None = None


@router.get("/canary/status")
async def get_canary_status(
    client_id: UUID | None = Query(default=None),
    auth: AuthContext = Depends(get_current_auth),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Operator canary readiness — never exposes secrets."""
    from app.automation.canary import canary_status

    return await canary_status(db, organization_id=auth.organization_id, client_id=client_id)


@router.get("/canary/history")
async def get_canary_history(
    limit: int = Query(default=50, ge=1, le=200),
    auth: AuthContext = Depends(get_current_auth),
    db: AsyncSession = Depends(get_db),
) -> dict:
    from app.automation.canary import list_canary_history

    return await list_canary_history(db, organization_id=auth.organization_id, limit=limit)


@router.post("/canary/dry-run")
async def post_canary_dry_run(
    body: CanaryDryRunBody,
    auth: AuthContext = Depends(require_permission(Permission.action_approve)),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Full canary decision path without creating a mutating AIAction."""
    from app.automation.canary import canary_dry_run

    provider = _normalize_provider_param(body.provider)
    result = await canary_dry_run(
        db,
        organization_id=auth.organization_id,
        provider=provider,
        action_type=body.action_type,
        campaign_id=body.campaign_id,
        external_campaign_id=body.external_campaign_id,
        client_id=body.client_id,
        actor_user_id=auth.user.id,
    )
    await db.commit()
    return result


@router.post("/canary/execute")
async def post_canary_execute(
    body: CanaryExecuteBody,
    auth: AuthContext = Depends(require_permission(Permission.action_execute)),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """
    Explicit live canary via ActionService → ExecutionEngine only.

    Requires I_CONFIRM_CANARY_LIVE_PROVIDER_EXECUTION and all canary gates.
    """
    from app.automation.canary import canary_execute

    provider = _normalize_provider_param(body.provider)
    result = await canary_execute(
        db,
        organization_id=auth.organization_id,
        provider=provider,
        action_type=body.action_type,
        campaign_id=body.campaign_id,
        external_campaign_id=body.external_campaign_id,
        client_id=body.client_id,
        actor_user_id=auth.user.id,
        confirm=body.confirm,
    )
    await db.commit()
    return result

