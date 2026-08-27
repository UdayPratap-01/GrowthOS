"""Layered production safety gates for autonomous mutations.

Defaults keep every gate CLOSED. Approval-driven operator flows use a narrower
set of checks so humans can still act under existing approval policy.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal
from uuid import UUID

from app.core.config import Settings, get_settings
from app.models.automation import AutonomySettings
from app.models.enums import AIActionType
from app.observability import events, metrics

GateIntent = Literal["autonomous", "approval", "execute"]


@dataclass
class GateCheck:
    name: str
    passed: bool
    detail: str
    code: str | None = None

    def as_dict(self) -> dict[str, Any]:
        body = {"name": self.name, "passed": self.passed, "detail": self.detail}
        if self.code:
            body["code"] = self.code
        return body


@dataclass
class GateResult:
    allowed: bool
    blocked_reason: str | None = None
    blocked_code: str | None = None
    checks: list[GateCheck] = field(default_factory=list)

    def add(self, name: str, passed: bool, detail: str, *, code: str | None = None) -> None:
        self.checks.append(GateCheck(name, passed, detail, code=code))
        if not passed and self.allowed:
            self.allowed = False
            self.blocked_reason = detail
            self.blocked_code = code


def _csv_set(raw: str | None) -> set[str]:
    if not raw or not str(raw).strip():
        return set()
    return {part.strip().lower() for part in str(raw).split(",") if part.strip()}


def _csv_uuid_set(raw: str | None) -> set[UUID]:
    out: set[UUID] = set()
    if not raw or not str(raw).strip():
        return out
    for part in str(raw).split(","):
        part = part.strip()
        if not part:
            continue
        try:
            out.add(UUID(part))
        except ValueError:
            continue
    return out


def normalize_provider(platform: str | None) -> str:
    p = (platform or "").strip().lower()
    if p in {"meta", "facebook", "instagram"}:
        return "meta"
    if p in {"google", "google_ads"}:
        return "google"
    return p


def evaluate_production_gates(
    *,
    organization_id: UUID,
    client_id: UUID | None,
    platform: str | None,
    action_type: AIActionType | str | None,
    autonomy: AutonomySettings,
    intent: GateIntent,
    app_settings: Settings | None = None,
    audit_db=None,
    actor_user_id: UUID | None = None,
) -> GateResult:
    """
    Evaluate layered gates.

    - autonomous: creating/executing without human approval for this mutation
    - approval: operator-approved create path (kill switch does not block create)
    - execute: claiming/running an action (kill switch blocks NEW autonomous exec;
      explicit operator execute still requires global+provider when live)
    """
    settings = app_settings or get_settings()
    result = GateResult(allowed=True)
    provider = normalize_provider(platform)
    action_value = (
        action_type.value if isinstance(action_type, AIActionType) else str(action_type or "")
    ).strip().lower()

    # Emergency kill switch — blocks NEW autonomous mutations only.
    kill = bool(settings.autonomous_kill_switch)
    if intent == "autonomous":
        result.add(
            "kill_switch",
            not kill,
            "AUTONOMOUS_KILL_SWITCH_ENABLED" if kill else "kill switch off",
            code="AUTONOMOUS_KILL_SWITCH_ENABLED" if kill else None,
        )
    else:
        result.add("kill_switch", True, "not applicable for approval/operator path", code=None)

    # Global autonomous execution latch
    global_on = bool(settings.autonomous_execution_enabled)
    if intent == "autonomous":
        result.add(
            "global_autonomous_execution",
            global_on,
            "AUTONOMOUS_EXECUTION_ENABLED" if global_on else "AUTONOMOUS_EXECUTION_DISABLED",
            code=None if global_on else "AUTONOMOUS_EXECUTION_DISABLED",
        )
    else:
        result.add(
            "global_autonomous_execution",
            True,
            "operator/approval path does not require AUTONOMOUS_EXECUTION_ENABLED",
        )

    # Optimization latch — required for closed-loop creates (both intents that create from recs)
    if intent in {"autonomous", "approval"}:
        opt_on = bool(settings.optimization_enabled)
        result.add(
            "optimization_enabled",
            opt_on,
            "enabled" if opt_on else "OPTIMIZATION_DISABLED",
            code=None if opt_on else "OPTIMIZATION_DISABLED",
        )

    # Provider autonomous latch (only for autonomous mutations)
    if intent == "autonomous":
        if provider == "meta":
            ok = bool(settings.meta_autonomous_enabled)
            result.add(
                "provider_autonomous",
                ok,
                "META_AUTONOMOUS_ENABLED" if ok else "META_AUTONOMOUS_DISABLED",
                code=None if ok else "META_AUTONOMOUS_DISABLED",
            )
        elif provider == "google":
            ok = bool(settings.google_autonomous_enabled)
            result.add(
                "provider_autonomous",
                ok,
                "GOOGLE_AUTONOMOUS_ENABLED" if ok else "GOOGLE_AUTONOMOUS_DISABLED",
                code=None if ok else "GOOGLE_AUTONOMOUS_DISABLED",
            )
        else:
            result.add(
                "provider_autonomous",
                False,
                f"unsupported provider for autonomous: {provider or 'missing'}",
                code="PROVIDER_GATE_BLOCKED",
            )

    # Organization / client automation
    result.add(
        "organization_automation",
        bool(autonomy.automation_enabled),
        "automation_enabled" if autonomy.automation_enabled else "ORGANIZATION_AUTOMATION_DISABLED",
        code=None if autonomy.automation_enabled else "ORGANIZATION_AUTOMATION_DISABLED",
    )

    # Action allowlist (empty = unrestricted at settings layer; canary may still restrict)
    allowed_actions = {str(a).strip().lower() for a in (autonomy.allowed_actions or []) if str(a).strip()}
    if allowed_actions:
        result.add(
            "action_allowlist",
            action_value in allowed_actions,
            f"action={action_value}",
            code=None if action_value in allowed_actions else "ACTION_ALLOWLIST_BLOCKED",
        )
    else:
        result.add("action_allowlist", True, "no org allowlist configured")

    # Canary — empty lists mean NO autonomous orgs/providers/actions (safe default)
    if intent == "autonomous":
        canary_orgs = _csv_uuid_set(settings.autonomous_canary_org_ids)
        org_ok = organization_id in canary_orgs
        result.add(
            "canary_organization",
            org_ok,
            f"org in canary={org_ok} (empty list means none)" if canary_orgs else "canary org list empty",
            code=None if org_ok else "CANARY_ORG_BLOCKED",
        )

        canary_providers = _csv_set(settings.autonomous_canary_providers)
        prov_ok = provider in canary_providers
        result.add(
            "canary_provider",
            prov_ok,
            f"provider={provider}" if canary_providers else "canary provider list empty",
            code=None if prov_ok else "CANARY_PROVIDER_BLOCKED",
        )

        canary_actions = _csv_set(settings.autonomous_canary_action_types)
        act_ok = action_value in canary_actions
        result.add(
            "canary_action_type",
            act_ok,
            f"action={action_value}" if canary_actions else "canary action list empty",
            code=None if act_ok else "CANARY_ACTION_BLOCKED",
        )

    # Observability + optional audit for blocks
    if not result.allowed:
        code = result.blocked_code or "PRODUCTION_GATE_BLOCKED"
        metrics.increment("autonomous_gate_blocks_total", labels={"code": code, "intent": intent})
        events.autonomous_gate_blocked(
            organization_id=organization_id,
            code=code,
            intent=intent,
            detail=result.blocked_reason,
        )
        if audit_db is not None and code:
            from app.security.audit import write_audit

            # Fire-and-forget style: caller awaits this function already sync path
            # so we schedule via coroutine only when audit_db provided as session —
            # callers that need audit should call record_gate_block_audit separately.
            _ = (audit_db, actor_user_id, client_id)  # reserved for callers

    return result


async def record_gate_block_audit(
    db,
    *,
    organization_id: UUID,
    actor_user_id: UUID | None,
    result: GateResult,
    resource_type: str,
    resource_id: str,
    trigger: str,
) -> None:
    from app.security.audit import write_audit

    code = result.blocked_code or "PRODUCTION_GATE_BLOCKED"
    action = {
        "AUTONOMOUS_KILL_SWITCH_ENABLED": "autonomous.kill_switch_blocked",
        "META_AUTONOMOUS_DISABLED": "autonomous.provider_gate_blocked",
        "GOOGLE_AUTONOMOUS_DISABLED": "autonomous.provider_gate_blocked",
        "ORGANIZATION_AUTOMATION_DISABLED": "autonomous.organization_gate_blocked",
        "CANARY_ORG_BLOCKED": "autonomous.organization_gate_blocked",
    }.get(code, "autonomous.gate_blocked")
    await write_audit(
        db,
        action=action,
        organization_id=organization_id,
        user_id=actor_user_id,
        resource_type=resource_type,
        resource_id=resource_id,
        details={
            "trigger": trigger,
            "code": code,
            "reason": (result.blocked_reason or "")[:300],
            "checks": [c.as_dict() for c in result.checks],
        },
    )


def validate_production_gate_settings(settings: Settings | None = None) -> list[str]:
    settings = settings or get_settings()
    errors: list[str] = []
    if settings.autonomous_max_daily_spend_impact < 0:
        errors.append("AUTONOMOUS_MAX_DAILY_SPEND_IMPACT must be >= 0")
    if settings.autonomous_max_campaigns_per_cycle < 1:
        errors.append("AUTONOMOUS_MAX_CAMPAIGNS_PER_CYCLE must be >= 1")
    if settings.optimization_max_evidence_age_hours < 1:
        errors.append("OPTIMIZATION_MAX_EVIDENCE_AGE_HOURS must be >= 1")
    # Soft warn via errors only for malformed UUIDs in canary list
    raw = (settings.autonomous_canary_org_ids or "").strip()
    if raw:
        for part in raw.split(","):
            part = part.strip()
            if not part:
                continue
            try:
                UUID(part)
            except ValueError:
                errors.append(f"AUTONOMOUS_CANARY_ORG_IDS contains invalid UUID: {part!r}")
    return errors
