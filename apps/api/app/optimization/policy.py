"""Deterministic optimization policy engine — never silently clamps dangerous actions."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.models.automation import AIAction, AutonomySettings
from app.models.enums import AIActionStatus, AIActionType, PerformanceRecommendationStatus, RiskLevel
from app.models.performance_intelligence import PerformanceRecommendation
from app.optimization.decision import ProposedAction
from app.optimization.risk import risk_allows_autonomous
from app.publishing.capabilities import CapabilityStatus, google_ads_capabilities, meta_ads_capabilities


@dataclass
class PolicyCheck:
    name: str
    passed: bool
    detail: str

    def as_dict(self) -> dict[str, Any]:
        return {"name": self.name, "passed": self.passed, "detail": self.detail}


@dataclass
class PolicyResult:
    allowed: bool
    blocked_reason: str | None = None
    checks: list[PolicyCheck] = field(default_factory=list)

    def add(self, name: str, passed: bool, detail: str) -> None:
        self.checks.append(PolicyCheck(name, passed, detail))
        if not passed and self.allowed:
            self.allowed = False
            self.blocked_reason = detail


OPPOSITE_ACTIONS = {
    AIActionType.pause_campaign: AIActionType.resume_campaign,
    AIActionType.resume_campaign: AIActionType.pause_campaign,
}


async def evaluate_policy(
    db: AsyncSession,
    *,
    organization_id: UUID,
    recommendation: PerformanceRecommendation,
    settings: AutonomySettings,
    proposal: ProposedAction,
    campaign,
    integration_connected: bool,
    credentials_configured: bool,
    app_settings: Settings | None = None,
) -> PolicyResult:
    """
    Explicit policy evaluation. Failures return BLOCKED with reasons —
    never silently reduce a 50% increase to 20%.
    """
    app_settings = app_settings or get_settings()
    result = PolicyResult(allowed=True)
    cur_vals = recommendation.current_values or {}

    # --- Sample / confidence ---
    spend = float(cur_vals.get("spend") or 0)
    impressions = int(cur_vals.get("impressions") or 0)
    clicks = int(cur_vals.get("clicks") or 0)
    conversions = float(cur_vals.get("conversions") or 0)
    confidence = float(recommendation.confidence or 0)

    result.add(
        "min_spend",
        spend >= app_settings.performance_min_spend,
        f"spend={spend} min={app_settings.performance_min_spend}",
    )
    result.add(
        "min_impressions",
        impressions >= app_settings.performance_min_impressions,
        f"impressions={impressions} min={app_settings.performance_min_impressions}",
    )
    if proposal.action_type in {AIActionType.update_budget, AIActionType.pause_campaign}:
        result.add(
            "min_clicks",
            clicks >= app_settings.performance_min_clicks or conversions >= app_settings.performance_min_conversions,
            f"clicks={clicks} conversions={conversions}",
        )
    result.add(
        "min_confidence",
        confidence >= app_settings.optimization_min_confidence,
        f"confidence={confidence} min={app_settings.optimization_min_confidence}",
    )

    # --- Evidence freshness (never invent metrics; reject stale evidence) ---
    created = recommendation.created_at
    if created is not None:
        if created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
        age_h = (datetime.now(timezone.utc) - created).total_seconds() / 3600.0
        max_age = float(app_settings.optimization_max_evidence_age_hours)
        result.add(
            "evidence_fresh",
            age_h <= max_age,
            f"evidence_age_hours={age_h:.1f} max={max_age}",
        )
    else:
        result.add("evidence_fresh", False, "recommendation.created_at missing")

    # --- Recommendation lifecycle ---
    status_value = (
        recommendation.status.value
        if hasattr(recommendation.status, "value")
        else str(recommendation.status)
    ).upper()
    if status_value == PerformanceRecommendationStatus.expired.value:
        result.add("not_expired", False, "Recommendation is EXPIRED")
    elif recommendation.expires_at and recommendation.expires_at < datetime.now(timezone.utc):
        result.add("not_expired", False, "Recommendation TTL elapsed")
    else:
        result.add("not_expired", True, "Recommendation still valid")

    if status_value == PerformanceRecommendationStatus.rejected.value:
        result.add("not_rejected", False, "Recommendation was REJECTED")
    else:
        result.add("not_rejected", True, "Recommendation not rejected")

    # --- Allowlist ---
    allowed_actions = set(settings.allowed_actions or [])
    result.add(
        "action_allowlist",
        not allowed_actions or proposal.action_type.value in allowed_actions,
        f"action={proposal.action_type.value}",
    )
    allowed_platforms = {p.lower() for p in (settings.allowed_platforms or [])}
    platform = (recommendation.platform or "").lower()
    result.add(
        "platform_allowlist",
        not allowed_platforms or platform in allowed_platforms,
        f"platform={platform}",
    )

    # --- Provider capability ---
    cap_ok, cap_detail = _provider_capability(
        platform=platform,
        action_type=proposal.action_type,
        connected=integration_connected,
        credentials_configured=credentials_configured,
    )
    result.add("provider_capability", cap_ok, cap_detail)

    # --- External ID / campaign ---
    external_id = None
    if campaign is not None:
        external_id = campaign.external_id or (campaign.metrics or {}).get("external_campaign_id")
    if not external_id:
        external_id = recommendation.external_campaign_id or None
    result.add(
        "external_id_present",
        bool(external_id),
        f"external_id={external_id or 'MISSING'}",
    )
    if campaign is None:
        result.add("campaign_resolved", False, "No internal Campaign matched external_campaign_id")
    else:
        result.add(
            "campaign_tenant",
            campaign.organization_id == organization_id,
            "Campaign belongs to organization",
        )

    # --- Budget policy (never clamp) ---
    if proposal.action_type == AIActionType.update_budget and proposal.daily_budget is not None:
        prev = Decimal(str(proposal.payload.get("previous_daily_budget") or 0))
        new = proposal.daily_budget
        pct = float(proposal.percentage or 0)
        direction = str(proposal.direction or "").upper()
        max_inc = float(settings.maximum_budget_increase_percentage or 0)
        max_dec = float(settings.maximum_budget_decrease_percentage or 0)

        if direction == "INCREASE":
            result.add(
                "max_budget_increase",
                pct <= max_inc + 1e-9,
                f"requested_increase={pct}% max_allowed={max_inc}% (not clamped)",
            )
        if direction == "DECREASE":
            result.add(
                "max_budget_decrease",
                pct <= max_dec + 1e-9,
                f"requested_decrease={pct}% max_allowed={max_dec}% (not clamped)",
            )

        min_budget = Decimal(str(app_settings.optimization_min_campaign_budget))
        max_budget = Decimal(str(settings.maximum_campaign_budget or 0))
        result.add(
            "min_campaign_budget",
            new >= min_budget,
            f"new_budget={new} min={min_budget}",
        )
        # Prevent accidental zero / negative
        result.add(
            "budget_positive",
            new > 0,
            f"new_budget={new} (must be > 0; never clamped)",
        )
        result.add(
            "max_campaign_budget",
            max_budget <= 0 or new <= max_budget,
            f"new_budget={new} max={max_budget}",
        )
        # Refuse silent clamping scenarios: if previous already at max and increase requested
        if direction == "INCREASE" and max_budget > 0 and prev >= max_budget:
            result.add("budget_headroom", False, "Campaign already at maximum_campaign_budget")

        # Absolute autonomous spend-impact ceiling (daily delta absolute)
        max_impact = Decimal(str(app_settings.autonomous_max_daily_spend_impact or 0))
        if max_impact > 0 and direction == "INCREASE":
            delta = new - prev
            result.add(
                "max_daily_spend_impact",
                delta <= max_impact,
                f"delta={delta} max_impact={max_impact} (not clamped)",
            )

    # --- Rate limit: autonomous actions per day ---
    since = datetime.now(timezone.utc) - timedelta(hours=24)
    from sqlalchemy import func

    recent_n = int(
        await db.scalar(
            select(func.count()).select_from(AIAction).where(
                AIAction.organization_id == organization_id,
                AIAction.agent == "closed_loop_optimizer",
                AIAction.created_at >= since,
            )
        )
        or 0
    )
    result.add(
        "max_actions_per_period",
        recent_n < app_settings.optimization_max_actions_per_day,
        f"recent={recent_n} max={app_settings.optimization_max_actions_per_day}",
    )

    # --- Cooldown same campaign / same action ---
    if campaign is not None:
        cooldown = timedelta(hours=app_settings.optimization_cooldown_hours)
        cutoff = datetime.now(timezone.utc) - cooldown
        same = await db.scalar(
            select(AIAction).where(
                AIAction.organization_id == organization_id,
                AIAction.target_id == str(campaign.id),
                AIAction.action_type == proposal.action_type,
                AIAction.created_at >= cutoff,
                AIAction.status.in_(
                    [
                        AIActionStatus.pending,
                        AIActionStatus.approved,
                        AIActionStatus.executing,
                        AIActionStatus.completed,
                    ]
                ),
            ).limit(1)
        )
        result.add(
            "cooldown_same_action",
            same is None,
            f"cooldown_hours={app_settings.optimization_cooldown_hours}",
        )

        # Opposite-action loop protection
        opposite = OPPOSITE_ACTIONS.get(proposal.action_type)
        if opposite:
            opp_cooldown = timedelta(hours=app_settings.optimization_opposite_cooldown_hours)
            opp_cutoff = datetime.now(timezone.utc) - opp_cooldown
            opp = await db.scalar(
                select(AIAction).where(
                    AIAction.organization_id == organization_id,
                    AIAction.target_id == str(campaign.id),
                    AIAction.action_type == opposite,
                    AIAction.created_at >= opp_cutoff,
                    AIAction.status.in_(
                        [
                            AIActionStatus.pending,
                            AIActionStatus.approved,
                            AIActionStatus.executing,
                            AIActionStatus.completed,
                        ]
                    ),
                ).limit(1)
            )
            result.add(
                "opposite_action_cooldown",
                opp is None,
                f"blocks {proposal.action_type.value} after recent {opposite.value}",
            )

        # Consecutive same-direction budget increases without new window
        if proposal.action_type == AIActionType.update_budget and str(proposal.direction).upper() == "INCREASE":
            consecutive = await _count_recent_budget_increases(
                db, organization_id=organization_id, campaign_id=str(campaign.id), hours=72
            )
            result.add(
                "max_consecutive_budget_increases",
                consecutive < app_settings.optimization_max_consecutive_budget_increases,
                f"consecutive={consecutive} max={app_settings.optimization_max_consecutive_budget_increases}",
            )

    # --- Duplicate recommendation action ---
    existing_for_rec = await _find_action_for_recommendation(
        db, organization_id=organization_id, recommendation_id=recommendation.id
    )
    # Allow if prior action failed (retry path) but block pending/completed for same rec
    if existing_for_rec is not None and existing_for_rec.status in {
        AIActionStatus.pending,
        AIActionStatus.approved,
        AIActionStatus.executing,
        AIActionStatus.completed,
    }:
        # Ambiguous reconciliation blocked retries are also not duplicable as new actions
        recon = (existing_for_rec.result or {}).get("reconciliation") or {}
        if recon.get("state") in {"PENDING", "UNKNOWN"}:
            result.add("no_duplicate_ambiguous", False, "Prior action is ambiguous PENDING/UNKNOWN")
        else:
            result.add(
                "no_duplicate_action",
                False,
                f"AIAction {existing_for_rec.id} already exists for recommendation",
            )
    else:
        result.add("no_duplicate_action", True, "No active AIAction for recommendation")

    # --- HIGH risk cannot be autonomous solely due to confidence ---
    # (enforced at decision layer; recorded here for audit)
    result.add(
        "risk_recorded",
        True,
        f"risk={proposal.risk_level.value}",
    )

    return result


def _provider_capability(
    *,
    platform: str,
    action_type: AIActionType,
    connected: bool,
    credentials_configured: bool,
) -> tuple[bool, str]:
    op_map = {
        AIActionType.pause_campaign: "pause",
        AIActionType.resume_campaign: "resume",
        AIActionType.update_budget: "update_budget",
    }
    op = op_map.get(action_type)
    if op is None:
        return False, f"No capability mapping for {action_type.value}"

    if platform in {"meta", "facebook", "instagram"}:
        matrix = meta_ads_capabilities(connected=connected, credentials_configured=credentials_configured)
    elif platform in {"google", "google_ads"}:
        matrix = google_ads_capabilities(connected=connected, credentials_configured=credentials_configured)
        # Google update_budget unsupported even when connected
        if action_type == AIActionType.update_budget and connected and credentials_configured:
            return False, "Google Ads UPDATE_BUDGET is unsupported"
    else:
        return False, f"Unsupported optimization platform {platform!r}"

    for cap in matrix.capabilities:
        if cap.operation == op:
            ok = cap.status == CapabilityStatus.supported
            return ok, f"{matrix.provider}:{op}={cap.status.value} {cap.message}"
    return False, f"Capability {op} not listed for provider"


async def _count_recent_budget_increases(
    db: AsyncSession, *, organization_id: UUID, campaign_id: str, hours: int
) -> int:
    from sqlalchemy import func

    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    rows = list(
        (
            await db.scalars(
                select(AIAction).where(
                    AIAction.organization_id == organization_id,
                    AIAction.target_id == campaign_id,
                    AIAction.action_type == AIActionType.update_budget,
                    AIAction.created_at >= cutoff,
                    AIAction.status.in_(
                        [
                            AIActionStatus.pending,
                            AIActionStatus.approved,
                            AIActionStatus.executing,
                            AIActionStatus.completed,
                        ]
                    ),
                )
            )
        ).all()
    )
    n = 0
    for row in rows:
        if str((row.payload or {}).get("budget_direction") or "").upper() == "INCREASE":
            n += 1
    return n


async def _find_action_for_recommendation(
    db: AsyncSession, *, organization_id: UUID, recommendation_id: UUID
) -> AIAction | None:
    rows = list(
        (
            await db.scalars(
                select(AIAction)
                .where(
                    AIAction.organization_id == organization_id,
                    AIAction.agent == "closed_loop_optimizer",
                )
                .order_by(AIAction.created_at.desc())
                .limit(100)
            )
        ).all()
    )
    rid = str(recommendation_id)
    for row in rows:
        if str((row.payload or {}).get("recommendation_id") or "") == rid:
            return row
    return None
