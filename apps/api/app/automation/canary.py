"""Controlled live provider canary — gates + dry-run + explicit execute via ActionService.

Empty allowlists mean NO execution. Never bypasses ActionService / ExecutionEngine.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.automation.idempotency import sanitize_platform_response
from app.core.config import Settings, get_settings
from app.integrations.persistence import get_integration_row
from app.models.automation import AIAction
from app.models.enums import AIActionStatus, AIActionType, Priority, RiskLevel
from app.models.marketing import Campaign
from app.observability import events, metrics
from app.optimization.risk import classify_optimization_risk
from app.publishing.ads_reconciliation import AdsReconciler
from app.publishing.capabilities import CapabilityStatus, google_ads_capabilities, meta_ads_capabilities
from app.publishing.provider_errors import reconciliation_blocks_retry
from app.schemas.autopilot import ActionDecision, AIActionCreate
from app.security.audit import write_audit
from app.services.action_service import ActionService
from app.services.autonomy_service import AutonomyService

CANARY_CONFIRM_PHRASE = "I_CONFIRM_CANARY_LIVE_PROVIDER_EXECUTION"
CANARY_AGENT = "canary_operator"

# Phase 2 preferred surface — budget only if explicitly allowlisted + capable.
DEFAULT_SAFE_ACTIONS = frozenset({"pause_campaign", "resume_campaign"})


@dataclass
class CanaryCheck:
    name: str
    passed: bool
    code: str | None
    detail: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "passed": self.passed,
            "code": self.code,
            "detail": self.detail,
            "status": "PASS" if self.passed else "BLOCKED",
        }


@dataclass
class CanaryGateResult:
    allowed: bool
    readiness: str  # NOT_CONFIGURED | DISABLED | BLOCKED | READY
    blocked_code: str | None = None
    blocked_reason: str | None = None
    checks: list[CanaryCheck] = field(default_factory=list)
    campaign: Campaign | None = None
    provider: str | None = None
    action_type: AIActionType | None = None
    risk: RiskLevel | None = None
    verification: dict[str, Any] = field(default_factory=dict)
    spend_impact: float = 0.0

    def add(self, name: str, passed: bool, detail: str, *, code: str | None = None) -> None:
        self.checks.append(CanaryCheck(name, passed, code if not passed else None, detail))
        if not passed and self.allowed:
            self.allowed = False
            self.blocked_code = code
            self.blocked_reason = detail
            if self.readiness not in {"NOT_CONFIGURED", "DISABLED"}:
                self.readiness = "BLOCKED"

    def as_dict(self) -> dict[str, Any]:
        return {
            "allowed": self.allowed,
            "readiness": self.readiness,
            "blocked_code": self.blocked_code,
            "blocked_reason": self.blocked_reason,
            "checks": [c.as_dict() for c in self.checks],
            "provider": self.provider,
            "action_type": self.action_type.value if self.action_type else None,
            "risk": self.risk.value if self.risk else None,
            "campaign_id": str(self.campaign.id) if self.campaign else None,
            "campaign_external_id": (
                self.campaign.external_id
                or (self.campaign.metrics or {}).get("external_campaign_id")
                if self.campaign
                else None
            ),
            "verification": sanitize_platform_response(self.verification),
            "spend_impact": self.spend_impact,
            "safe_for_mutation": False,  # only true after execute path intentionally runs
        }


def _csv_set(raw: str | None) -> set[str]:
    if not raw or not str(raw).strip():
        return set()
    return {p.strip().lower() for p in str(raw).split(",") if p.strip()}


def _csv_uuid_set(raw: str | None) -> set[UUID]:
    out: set[UUID] = set()
    for part in (raw or "").split(","):
        part = part.strip()
        if not part:
            continue
        try:
            out.add(UUID(part))
        except ValueError:
            continue
    return out


def _norm_provider(provider: str | None) -> str:
    p = (provider or "").strip().lower()
    if p in {"meta", "facebook", "instagram"}:
        return "meta"
    if p in {"google", "google_ads"}:
        return "google_ads"
    return p


def _parse_action_type(raw: str | None) -> AIActionType | None:
    """Accept pause_campaign / PAUSE_CAMPAIGN (enum name or value)."""
    key = (raw or "").strip()
    if not key:
        return None
    try:
        return AIActionType(key.upper())
    except ValueError:
        pass
    lowered = key.lower()
    for member in AIActionType:
        if member.name == lowered or member.value.lower() == lowered:
            return member
    return None


def _action_aliases(atype: AIActionType) -> set[str]:
    return {atype.name.lower(), atype.value.lower()}


def require_canary_confirm(confirm: str | None) -> tuple[bool, str | None]:
    if (confirm or "").strip() != CANARY_CONFIRM_PHRASE:
        return False, f"confirm must equal {CANARY_CONFIRM_PHRASE!r}"
    return True, None


def _parse_checked_at(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except ValueError:
        return None


async def evaluate_canary_gate(
    db: AsyncSession,
    *,
    organization_id: UUID,
    provider: str,
    action_type: str,
    campaign_id: UUID | None = None,
    external_campaign_id: str | None = None,
    client_id: UUID | None = None,
    settings: Settings | None = None,
) -> CanaryGateResult:
    """Authoritative canary gate — structured ALLOW/BLOCK codes."""
    settings = settings or get_settings()
    result = CanaryGateResult(allowed=True, readiness="READY")
    provider = _norm_provider(provider)
    result.provider = provider

    # --- Master switches (before action parse so DISABLED wins over unknown action) ---
    if not settings.canary_enabled:
        result.readiness = "DISABLED"
        result.add("canary_enabled", False, "CANARY_ENABLED=false", code="BLOCKED_CANARY_DISABLED")
        return result

    if settings.autonomous_kill_switch:
        result.add("kill_switch", False, "AUTONOMOUS_KILL_SWITCH active", code="BLOCKED_KILL_SWITCH")
        return result

    env = settings.env
    allowed_envs = _csv_set(settings.canary_allowed_environments)
    if not allowed_envs:
        result.readiness = "NOT_CONFIGURED"
        result.add(
            "environment",
            False,
            "canary_allowed_environments empty (none permitted)",
            code="BLOCKED_CANARY_DISABLED",
        )
        return result
    result.add(
        "environment",
        env in allowed_envs,
        f"env={env} allowed={sorted(allowed_envs)}",
        code="BLOCKED_INVALID_RESOURCE" if env not in allowed_envs else None,
    )

    atype = _parse_action_type(action_type)
    if atype is None:
        result.add(
            "action_type",
            False,
            f"Unknown action {action_type}",
            code="BLOCKED_ACTION_NOT_ALLOWLISTED",
        )
        result.readiness = "BLOCKED"
        return result
    result.action_type = atype
    result.risk = classify_optimization_risk(action_type=atype)
    action_aliases = _action_aliases(atype)

    # --- Allowlists (empty = deny) ---
    orgs = _csv_uuid_set(settings.canary_allowed_org_ids)
    if not orgs:
        result.readiness = "NOT_CONFIGURED"
        result.add("org_allowlist", False, "canary_allowed_org_ids empty", code="BLOCKED_ORG_NOT_ALLOWLISTED")
        return result
    result.add(
        "org_allowlist",
        organization_id in orgs,
        "organization allowlisted" if organization_id in orgs else "organization not allowlisted",
        code="BLOCKED_ORG_NOT_ALLOWLISTED" if organization_id not in orgs else None,
    )

    providers = _csv_set(settings.canary_allowed_providers)
    if not providers:
        result.readiness = "NOT_CONFIGURED"
        result.add("provider_allowlist", False, "canary_allowed_providers empty", code="BLOCKED_PROVIDER_DISABLED")
        return result
    provider_ok = provider in providers or (provider == "google_ads" and "google" in providers)
    result.add(
        "provider_allowlist",
        provider_ok,
        f"provider={provider}",
        code="BLOCKED_PROVIDER_DISABLED" if not provider_ok else None,
    )

    actions = _csv_set(settings.canary_allowed_actions)
    if not actions:
        result.readiness = "NOT_CONFIGURED"
        result.add("action_allowlist", False, "canary_allowed_actions empty", code="BLOCKED_ACTION_NOT_ALLOWLISTED")
        return result
    action_ok = bool(action_aliases & actions)
    result.add(
        "action_allowlist",
        action_ok,
        f"action={atype.value}",
        code="BLOCKED_ACTION_NOT_ALLOWLISTED" if not action_ok else None,
    )

    # Prefer pause/resume; budget only if allowlisted AND capable later
    if atype == AIActionType.update_budget and provider == "google_ads":
        result.add(
            "google_budget",
            False,
            "Google budget update unsupported",
            code="BLOCKED_CAPABILITY",
        )

    autonomy = await AutonomyService(db).get_effective(organization_id, client_id)
    result.add(
        "automation_enabled",
        bool(autonomy.automation_enabled),
        "organization automation_enabled required for ActionService financial actions",
        code="BLOCKED_POLICY" if not autonomy.automation_enabled else None,
    )
    allowed_org_actions = {str(a).strip().lower() for a in (autonomy.allowed_actions or []) if str(a).strip()}
    if allowed_org_actions:
        org_action_ok = bool(action_aliases & allowed_org_actions)
        result.add(
            "org_allowed_actions",
            org_action_ok,
            "action permitted by AutonomySettings.allowed_actions",
            code="BLOCKED_POLICY" if not org_action_ok else None,
        )

    # Spend impact (pause/resume = 0 budget impact; still HIGH business risk for pause)
    if atype in {AIActionType.pause_campaign, AIActionType.resume_campaign}:
        result.spend_impact = 0.0
        result.add(
            "spend_impact",
            True,
            "budget_impact=0 (pause/resume); business risk still classified separately",
        )
    else:
        max_spend = float(settings.canary_max_spend_impact or 0)
        result.spend_impact = 0.0
        ok_spend = result.spend_impact <= max_spend
        result.add(
            "spend_impact",
            ok_spend,
            f"spend_impact={result.spend_impact} max={max_spend}",
            code="BLOCKED_SPEND_LIMIT" if not ok_spend else None,
        )

    # Resolve campaign (tenant-owned)
    campaign = await _resolve_campaign(
        db,
        organization_id=organization_id,
        campaign_id=campaign_id,
        external_campaign_id=external_campaign_id,
        client_id=client_id,
        provider=provider,
    )
    if campaign is None:
        result.add(
            "campaign_resolved",
            False,
            "Campaign not found for organization",
            code="BLOCKED_INVALID_RESOURCE",
        )
        return result
    result.campaign = campaign
    if campaign.organization_id != organization_id:
        result.add("tenant", False, "Campaign tenant mismatch", code="BLOCKED_INVALID_RESOURCE")
        return result

    ext = campaign.external_id or (campaign.metrics or {}).get("external_campaign_id")
    if not ext:
        result.add("external_id", False, "Campaign missing external_id", code="BLOCKED_INVALID_RESOURCE")
    else:
        result.add("external_id", True, f"external_id present")

    # Account / campaign allowlists
    if provider == "meta":
        accounts = _csv_set(settings.canary_allowed_meta_ad_accounts)
        camps = _csv_set(settings.canary_allowed_meta_campaigns)
        if not accounts:
            result.add("account_allowlist", False, "meta ad accounts allowlist empty", code="BLOCKED_ACCOUNT_NOT_ALLOWLISTED")
        else:
            # Match against integration config or campaign metrics
            acct = str((campaign.metrics or {}).get("ad_account_id") or (campaign.metrics or {}).get("account_id") or "")
            row = await get_integration_row(db, organization_id=organization_id, provider="meta", client_id=client_id)
            if (not row) and client_id:
                row = await get_integration_row(db, organization_id=organization_id, provider="meta", client_id=None)
            cfg_acct = str((row.config or {}).get("external_account_id") or "") if row else ""
            cfg_accounts = []
            if row:
                for a in (row.config or {}).get("ad_accounts") or []:
                    if isinstance(a, dict) and a.get("id"):
                        cfg_accounts.append(str(a.get("id")))
            candidates = {
                acct.lower(),
                cfg_acct.lower(),
                acct.replace("act_", "").lower(),
                cfg_acct.replace("act_", "").lower(),
                *[c.lower() for c in cfg_accounts],
                *[c.replace("act_", "").lower() for c in cfg_accounts],
            }
            candidates.discard("")
            expanded = set(accounts)
            for a in list(accounts):
                if a.startswith("act_"):
                    expanded.add(a[4:])
                else:
                    expanded.add(f"act_{a}")
            ok = bool(candidates & expanded)
            result.add(
                "account_allowlist",
                ok,
                "meta ad account allowlisted" if ok else "meta ad account not allowlisted",
                code="BLOCKED_ACCOUNT_NOT_ALLOWLISTED" if not ok else None,
            )
        if not camps:
            result.add("campaign_allowlist", False, "meta campaigns allowlist empty", code="BLOCKED_CAMPAIGN_NOT_ALLOWLISTED")
        else:
            ok = str(ext).lower() in camps
            result.add(
                "campaign_allowlist",
                ok,
                f"campaign {ext}",
                code="BLOCKED_CAMPAIGN_NOT_ALLOWLISTED" if not ok else None,
            )
    else:
        customers = _csv_set(settings.canary_allowed_google_customers)
        camps = _csv_set(settings.canary_allowed_google_campaigns)
        if not customers:
            result.add("account_allowlist", False, "google customers allowlist empty", code="BLOCKED_ACCOUNT_NOT_ALLOWLISTED")
        else:
            cust = str((campaign.metrics or {}).get("customer_id") or "")
            row = await get_integration_row(
                db, organization_id=organization_id, provider="google_ads", client_id=client_id
            )
            if (not row) and client_id:
                row = await get_integration_row(
                    db, organization_id=organization_id, provider="google_ads", client_id=None
                )
            cfg_cust = str((row.config or {}).get("external_account_id") or (row.config or {}).get("customer_id") or "") if row else ""
            cfg_customers: list[str] = []
            if row:
                for c in (row.config or {}).get("customers") or []:
                    if isinstance(c, dict) and c.get("id"):
                        cfg_customers.append(str(c.get("id")).replace("-", ""))
            candidates = {
                cust.replace("-", ""),
                cfg_cust.replace("-", ""),
                *cfg_customers,
            }
            candidates.discard("")
            expanded = {c.replace("-", "") for c in customers}
            ok = bool(candidates & expanded)
            result.add(
                "account_allowlist",
                ok,
                "google customer allowlisted" if ok else "google customer not allowlisted",
                code="BLOCKED_ACCOUNT_NOT_ALLOWLISTED" if not ok else None,
            )
        if not camps:
            result.add("campaign_allowlist", False, "google campaigns allowlist empty", code="BLOCKED_CAMPAIGN_NOT_ALLOWLISTED")
        else:
            ok = str(ext).lower() in camps or str(ext).replace("-", "") in {c.replace("-", "") for c in camps}
            result.add(
                "campaign_allowlist",
                ok,
                f"campaign {ext}",
                code="BLOCKED_CAMPAIGN_NOT_ALLOWLISTED" if not ok else None,
            )

    # Integration connected
    int_provider = "meta" if provider == "meta" else "google_ads"
    row = await get_integration_row(db, organization_id=organization_id, provider=int_provider, client_id=client_id)
    if (not row or not row.secret_ref) and client_id is not None:
        row = await get_integration_row(db, organization_id=organization_id, provider=int_provider, client_id=None)
    connected = bool(row and row.secret_ref)
    result.add(
        "provider_connected",
        connected,
        "integration connected" if connected else "provider not connected",
        code="BLOCKED_PROVIDER_DISABLED" if not connected else None,
    )

    # Capability
    cfg = get_settings()
    if provider == "meta":
        creds = bool(cfg.meta_app_id and cfg.meta_app_secret)
        matrix = meta_ads_capabilities(connected=connected, credentials_configured=creds)
        op = "pause" if atype == AIActionType.pause_campaign else (
            "resume" if atype == AIActionType.resume_campaign else "update_budget"
        )
    else:
        creds = bool(cfg.google_client_id and cfg.google_client_secret and cfg.google_ads_developer_token)
        matrix = google_ads_capabilities(connected=connected, credentials_configured=creds)
        op = "pause" if atype == AIActionType.pause_campaign else (
            "resume" if atype == AIActionType.resume_campaign else "update_budget"
        )
    cap = next((c for c in matrix.capabilities if c.operation == op), None)
    cap_ok = cap is not None and cap.status == CapabilityStatus.supported
    result.add(
        "capability",
        cap_ok,
        cap.message if cap else f"no capability for {op}",
        code="BLOCKED_CAPABILITY" if not cap_ok else None,
    )

    # Verification freshness
    last = (row.config or {}).get("last_verification") if row else None
    result.verification = dict(last or {})
    if not last or last.get("status") != "VERIFIED":
        result.add(
            "verification",
            False,
            "Provider not VERIFIED (run read-only verification first)",
            code="BLOCKED_PROVIDER_NOT_VERIFIED",
        )
    else:
        checked = _parse_checked_at(last.get("checked_at"))
        max_age = timedelta(hours=max(1, int(settings.provider_verification_max_age_hours)))
        fresh = checked is not None and datetime.now(timezone.utc) - checked <= max_age
        result.add(
            "verification_fresh",
            fresh,
            f"verification age ok (max_h={settings.provider_verification_max_age_hours})"
            if fresh
            else "verification stale — re-run read-only verify",
            code="BLOCKED_STALE_VERIFICATION" if not fresh else None,
        )
        if last.get("safe_for_mutation") is True:
            # Phase 1 always false; ignore if somehow true
            pass

    # Risk: canary allows HIGH for pause only with explicit action allowlist (already checked)
    # Still record risk — HIGH is allowed for canary pause because human-confirmed
    result.add("risk_recorded", True, f"risk={result.risk.value if result.risk else 'unknown'}")

    # Daily canary limits
    since = datetime.now(timezone.utc) - timedelta(hours=24)
    n = int(
        await db.scalar(
            select(func.count()).select_from(AIAction).where(
                AIAction.organization_id == organization_id,
                AIAction.agent == CANARY_AGENT,
                AIAction.created_at >= since,
            )
        )
        or 0
    )
    max_day = max(1, int(settings.canary_max_actions_per_day))
    result.add(
        "daily_limit",
        n < max_day,
        f"canary_actions_24h={n} max={max_day}",
        code="BLOCKED_DAILY_LIMIT" if n >= max_day else None,
    )

    # Ambiguous / open actions on same campaign
    if campaign is not None:
        open_actions = list(
            (
                await db.scalars(
                    select(AIAction).where(
                        AIAction.organization_id == organization_id,
                        AIAction.target_id == str(campaign.id),
                        AIAction.action_type == atype,
                        AIAction.status.in_(
                            [
                                AIActionStatus.pending,
                                AIActionStatus.approved,
                                AIActionStatus.executing,
                            ]
                        ),
                    ).limit(5)
                )
            ).all()
        )
        if open_actions:
            result.add(
                "no_open_duplicate",
                False,
                f"open action {open_actions[0].id} blocks canary",
                code="BLOCKED_DUPLICATE",
            )
        else:
            result.add("no_open_duplicate", True, "no open duplicate")

        ambiguous = list(
            (
                await db.scalars(
                    select(AIAction).where(
                        AIAction.organization_id == organization_id,
                        AIAction.target_id == str(campaign.id),
                        AIAction.status == AIActionStatus.failed,
                    ).limit(20)
                )
            ).all()
        )
        blocked_amb = [a for a in ambiguous if reconciliation_blocks_retry(a)]
        if blocked_amb:
            result.add(
                "reconciliation_clean",
                False,
                f"ambiguous action {blocked_amb[0].id} blocks canary",
                code="BLOCKED_RECONCILIATION",
            )
        else:
            result.add("reconciliation_clean", True, "no PENDING/UNKNOWN reconciliation")

    if result.allowed:
        result.readiness = "READY"
    elif result.readiness not in {"DISABLED", "NOT_CONFIGURED"}:
        result.readiness = "BLOCKED"
    return result


async def _resolve_campaign(
    db: AsyncSession,
    *,
    organization_id: UUID,
    campaign_id: UUID | None,
    external_campaign_id: str | None,
    client_id: UUID | None,
    provider: str,
) -> Campaign | None:
    if campaign_id is not None:
        row = await db.scalar(
            select(Campaign).where(Campaign.id == campaign_id, Campaign.organization_id == organization_id)
        )
        return row
    if external_campaign_id:
        row = await db.scalar(
            select(Campaign).where(
                Campaign.organization_id == organization_id,
                Campaign.external_id == external_campaign_id,
            ).limit(1)
        )
        if row:
            return row
        # Google metrics fallback
        platform = "meta" if provider == "meta" else "google"
        rows = list(
            (
                await db.scalars(
                    select(Campaign).where(
                        Campaign.organization_id == organization_id,
                        Campaign.platform.in_([platform, "google_ads", "meta", "facebook"]),
                    ).limit(50)
                )
            ).all()
        )
        for camp in rows:
            if str((camp.metrics or {}).get("external_campaign_id") or "") == external_campaign_id:
                return camp
    return None


async def canary_status(
    db: AsyncSession,
    *,
    organization_id: UUID,
    client_id: UUID | None = None,
) -> dict[str, Any]:
    settings = get_settings()
    since = datetime.now(timezone.utc) - timedelta(hours=24)
    used = int(
        await db.scalar(
            select(func.count()).select_from(AIAction).where(
                AIAction.organization_id == organization_id,
                AIAction.agent == CANARY_AGENT,
                AIAction.created_at >= since,
            )
        )
        or 0
    )
    providers_out = []
    for provider in ("meta", "google_ads"):
        row = await get_integration_row(db, organization_id=organization_id, provider=provider, client_id=client_id)
        if (not row) and client_id:
            row = await get_integration_row(db, organization_id=organization_id, provider=provider, client_id=None)
        last = (row.config or {}).get("last_verification") if row else None
        providers_out.append(
            {
                "provider": provider,
                "connected": bool(row and row.secret_ref),
                "verification_status": (last or {}).get("status"),
                "verification_checked_at": (last or {}).get("checked_at"),
                "account_hint": (row.config or {}).get("account_label") if row else None,
            }
        )

    readiness = "DISABLED"
    if settings.canary_enabled:
        if not _csv_uuid_set(settings.canary_allowed_org_ids) or not _csv_set(settings.canary_allowed_actions):
            readiness = "NOT_CONFIGURED"
        elif settings.autonomous_kill_switch:
            readiness = "BLOCKED"
        elif organization_id not in _csv_uuid_set(settings.canary_allowed_org_ids):
            readiness = "BLOCKED"
        else:
            readiness = "READY"

    return {
        "canary_enabled": settings.canary_enabled,
        "readiness": readiness,
        "environment": settings.env,
        "kill_switch": settings.autonomous_kill_switch,
        "autonomous_execution_enabled": settings.autonomous_execution_enabled,
        "optimization_enabled": settings.optimization_enabled,
        "allowlists": {
            "orgs_configured": bool(_csv_uuid_set(settings.canary_allowed_org_ids)),
            "providers": settings.canary_allowed_providers,
            "actions": settings.canary_allowed_actions,
            "environments": settings.canary_allowed_environments,
            "meta_ad_accounts_configured": bool(_csv_set(settings.canary_allowed_meta_ad_accounts)),
            "meta_campaigns_configured": bool(_csv_set(settings.canary_allowed_meta_campaigns)),
            "google_customers_configured": bool(_csv_set(settings.canary_allowed_google_customers)),
            "google_campaigns_configured": bool(_csv_set(settings.canary_allowed_google_campaigns)),
        },
        "limits": {
            "max_actions_per_run": settings.canary_max_actions_per_run,
            "max_actions_per_day": settings.canary_max_actions_per_day,
            "max_spend_impact": settings.canary_max_spend_impact,
            "actions_used_24h": used,
            "actions_remaining_24h": max(0, settings.canary_max_actions_per_day - used),
            "verification_max_age_hours": settings.provider_verification_max_age_hours,
        },
        "providers": providers_out,
        "eligible_actions": sorted(_csv_set(settings.canary_allowed_actions)),
        "preferred_actions": sorted(DEFAULT_SAFE_ACTIONS),
        "confirm_phrase": CANARY_CONFIRM_PHRASE,
        "notes": [
            "Provider VERIFIED ≠ autonomous spend enabled.",
            "Canary success ≠ unrestricted production autonomy.",
            "Empty allowlists block all canary execution.",
            "KILL SWITCH blocks NEW live mutations.",
        ],
    }


async def canary_dry_run(
    db: AsyncSession,
    *,
    organization_id: UUID,
    provider: str,
    action_type: str,
    campaign_id: UUID | None,
    external_campaign_id: str | None,
    client_id: UUID | None,
    actor_user_id: UUID | None,
) -> dict[str, Any]:
    """Full decision path without ActionService mutation create."""
    gate = await evaluate_canary_gate(
        db,
        organization_id=organization_id,
        provider=provider,
        action_type=action_type,
        campaign_id=campaign_id,
        external_campaign_id=external_campaign_id,
        client_id=client_id,
    )
    metrics.increment(
        "canary_dry_run_total",
        labels={"provider": gate.provider or provider, "allowed": str(gate.allowed).lower()},
    )
    await write_audit(
        db,
        action="canary.dry_run" if gate.allowed else "canary.blocked",
        organization_id=organization_id,
        user_id=actor_user_id,
        resource_type="canary",
        resource_id=str(gate.campaign.id) if gate.campaign else provider,
        details=sanitize_platform_response(
            {
                "allowed": gate.allowed,
                "blocked_code": gate.blocked_code,
                "action_type": action_type,
                "provider": gate.provider,
                "readiness": gate.readiness,
                "dry_run": True,
            }
        ),
    )
    events.canary_lifecycle(
        organization_id=organization_id,
        outcome="dry_run_ok" if gate.allowed else f"blocked:{gate.blocked_code}",
        provider=gate.provider or provider,
    )
    plan = None
    if gate.campaign and gate.action_type:
        plan = {
            "action_type": gate.action_type.value,
            "platform": "meta" if gate.provider == "meta" else "google_ads",
            "target_id": str(gate.campaign.id),
            "external_id": gate.campaign.external_id,
            "risk": gate.risk.value if gate.risk else None,
            "spend_impact": gate.spend_impact,
            "would_call": "ActionService.create → approve → ExecutionEngine",
            "mutation": False,
        }
    return {
        "eligible": gate.allowed,
        "dry_run": True,
        "mutation": False,
        "gate": gate.as_dict(),
        "proposed_action": plan,
        "evidence": {
            "verification": sanitize_platform_response(gate.verification),
            "campaign_name": gate.campaign.name if gate.campaign else None,
        },
    }


async def canary_execute(
    db: AsyncSession,
    *,
    organization_id: UUID,
    provider: str,
    action_type: str,
    campaign_id: UUID | None,
    external_campaign_id: str | None,
    client_id: UUID | None,
    actor_user_id: UUID,
    confirm: str | None,
) -> dict[str, Any]:
    """Explicit live canary — create+approve via ActionService only."""
    ok, reason = require_canary_confirm(confirm)
    if not ok:
        await write_audit(
            db,
            action="canary.blocked",
            organization_id=organization_id,
            user_id=actor_user_id,
            resource_type="canary",
            resource_id=provider,
            details={"blocked_code": "BLOCKED_INVALID_CONFIRM", "reason": reason},
        )
        return {
            "executed": False,
            "blocked_code": "BLOCKED_INVALID_CONFIRM",
            "blocked_reason": reason,
            "mutation": False,
        }

    # Re-evaluate kill switch immediately before execute
    settings = get_settings()
    if settings.autonomous_kill_switch:
        return {
            "executed": False,
            "blocked_code": "BLOCKED_KILL_SWITCH",
            "blocked_reason": "AUTONOMOUS_KILL_SWITCH active",
            "mutation": False,
        }

    gate = await evaluate_canary_gate(
        db,
        organization_id=organization_id,
        provider=provider,
        action_type=action_type,
        campaign_id=campaign_id,
        external_campaign_id=external_campaign_id,
        client_id=client_id,
        settings=settings,
    )
    if not gate.allowed or gate.campaign is None or gate.action_type is None:
        metrics.increment(
            "canary_blocked_total",
            labels={"code": gate.blocked_code or "BLOCKED", "provider": gate.provider or provider},
        )
        await write_audit(
            db,
            action="canary.blocked",
            organization_id=organization_id,
            user_id=actor_user_id,
            resource_type="canary",
            resource_id=str(gate.campaign.id) if gate.campaign else provider,
            details={"blocked_code": gate.blocked_code, "checks": [c.as_dict() for c in gate.checks]},
        )
        return {
            "executed": False,
            "blocked_code": gate.blocked_code,
            "blocked_reason": gate.blocked_reason,
            "gate": gate.as_dict(),
            "mutation": False,
        }

    await write_audit(
        db,
        action="canary.execution_requested",
        organization_id=organization_id,
        user_id=actor_user_id,
        resource_type="canary",
        resource_id=str(gate.campaign.id),
        details={"action_type": gate.action_type.value, "provider": gate.provider},
    )
    metrics.increment("canary_execution_attempts_total", labels={"provider": gate.provider or provider})

    platform = "meta" if gate.provider == "meta" else "google_ads"
    payload = {
        "canary": True,
        "idempotency_key": f"canary:{organization_id}:{gate.campaign.id}:{gate.action_type.value}",
        "trigger": "operator_canary",
    }
    created = await ActionService(db).create(
        organization_id,
        AIActionCreate(
            action_type=gate.action_type,
            client_id=gate.campaign.client_id or client_id,
            agent=CANARY_AGENT,
            platform=platform,
            target_id=str(gate.campaign.id),
            description=f"Canary {gate.action_type.value} on {gate.campaign.name}",
            reason="Explicit operator canary live provider execution",
            evidence=[{"gate": gate.as_dict(), "verification_status": gate.verification.get("status")}],
            expected_impact="Post-action verification required",
            estimated_cost=Decimal("0"),
            risk_level=gate.risk or RiskLevel.high,
            priority=Priority.high,
            payload=payload,
            demo_mode=False,
        ),
        user_id=actor_user_id,
    )

    # Approve triggers ExecutionEngine via existing ActionService.approve
    await write_audit(
        db,
        action="canary.execution_started",
        organization_id=organization_id,
        user_id=actor_user_id,
        resource_type="ai_action",
        resource_id=str(created.id),
        details={"action_type": gate.action_type.value},
    )
    executed = await ActionService(db).approve(
        organization_id,
        created.id,
        actor_user_id,
        ActionDecision(note="Canary explicit confirmation"),
    )

    # Post-action verification (read-only) via existing reconciler
    action_row = await ActionService(db).get(organization_id, executed.id)
    post = await _post_action_verify(db, action=action_row, campaign=gate.campaign)
    if post.get("outcome") == "CONFIRMED_SUCCESS":
        await write_audit(
            db,
            action="canary.post_verification_succeeded",
            organization_id=organization_id,
            user_id=actor_user_id,
            resource_type="ai_action",
            resource_id=str(executed.id),
            details=sanitize_platform_response(post),
        )
        metrics.increment("canary_post_verification_total", labels={"outcome": "success", "provider": platform})
    elif post.get("outcome") in {"UNKNOWN", "UNSUPPORTED"} or action_row.status == AIActionStatus.failed:
        await write_audit(
            db,
            action="canary.reconciliation_required"
            if reconciliation_blocks_retry(action_row) or post.get("outcome") == "UNKNOWN"
            else "canary.post_verification_failed",
            organization_id=organization_id,
            user_id=actor_user_id,
            resource_type="ai_action",
            resource_id=str(executed.id),
            details=sanitize_platform_response(post),
        )
        metrics.increment(
            "canary_post_verification_total",
            labels={"outcome": "failed", "provider": platform},
        )
    else:
        await write_audit(
            db,
            action="canary.post_verification_failed",
            organization_id=organization_id,
            user_id=actor_user_id,
            resource_type="ai_action",
            resource_id=str(executed.id),
            details=sanitize_platform_response(post),
        )

    success = executed.status == AIActionStatus.completed and post.get("outcome") == "CONFIRMED_SUCCESS"
    await write_audit(
        db,
        action="canary.execution_succeeded" if success else "canary.execution_failed",
        organization_id=organization_id,
        user_id=actor_user_id,
        resource_type="ai_action",
        resource_id=str(executed.id),
        details={"status": executed.status, "post": sanitize_platform_response(post)},
    )
    metrics.increment(
        "canary_execution_total",
        labels={"provider": platform, "success": str(success).lower()},
    )
    events.canary_lifecycle(
        organization_id=organization_id,
        outcome="succeeded" if success else "failed",
        provider=platform,
    )

    return {
        "executed": True,
        "mutation": True,
        "action": executed.model_dump(mode="json") if hasattr(executed, "model_dump") else executed,
        "gate": gate.as_dict(),
        "post_verification": sanitize_platform_response(post),
        "reconciliation_blocks_retry": reconciliation_blocks_retry(action_row),
    }


async def _post_action_verify(db: AsyncSession, *, action: AIAction, campaign: Campaign) -> dict[str, Any]:
    """Read-only post-check using AdsReconciler — never retries mutation."""
    try:
        result = await AdsReconciler(db).reconcile(action, campaign=campaign)
        return {
            "outcome": result.outcome.value,
            "message": result.message,
            "observed_state": sanitize_platform_response(result.observed_state),
            "provider": result.provider,
        }
    except Exception as exc:
        return {"outcome": "UNKNOWN", "message": type(exc).__name__, "observed_state": {}}


async def list_canary_history(
    db: AsyncSession,
    *,
    organization_id: UUID,
    limit: int = 50,
) -> dict[str, Any]:
    rows = list(
        (
            await db.scalars(
                select(AIAction)
                .where(
                    AIAction.organization_id == organization_id,
                    AIAction.agent == CANARY_AGENT,
                )
                .order_by(AIAction.created_at.desc())
                .limit(limit)
            )
        ).all()
    )
    items = []
    for a in rows:
        recon = (a.result or {}).get("reconciliation") or {}
        items.append(
            {
                "action_id": str(a.id),
                "action_type": a.action_type.value,
                "provider": a.platform,
                "campaign_target": a.target_id,
                "status": a.status.value,
                "risk": a.risk_level.value if a.risk_level else None,
                "created_at": a.created_at.isoformat() if a.created_at else None,
                "error": a.error,
                "reconciliation_state": recon.get("state"),
                "dry_run": False,
                "live": True,
            }
        )
    return {"items": items, "total": len(items)}
