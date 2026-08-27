"""Map PerformanceRecommendation → proposed executable decision (no execution)."""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any
from uuid import UUID

from app.models.enums import AIActionType
from app.models.performance_intelligence import PerformanceRecommendation
from app.optimization.risk import classify_optimization_risk


# Operations that AdsExecutor can actually perform (provider-dependent later).
EXECUTABLE_OPERATIONS = {
    "UPDATE_BUDGET": AIActionType.update_budget,
    "PAUSE_CAMPAIGN": AIActionType.pause_campaign,
    "RESUME_CAMPAIGN": AIActionType.resume_campaign,
}


@dataclass
class ProposedAction:
    action_type: AIActionType
    direction: str | None
    percentage: float | None
    daily_budget: Decimal | None
    risk_level: Any
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass
class OptimizationDecision:
    decision: str  # ACTION | APPROVAL_REQUIRED | NO_ACTION | BLOCKED
    action_type: str | None
    reason: str
    confidence: float
    risk: str
    policy_checks: list[dict[str, Any]]
    recommendation_id: UUID
    evidence: dict[str, Any]
    proposed: ProposedAction | None = None
    autonomy_mode: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "decision": self.decision,
            "action_type": self.action_type,
            "reason": self.reason,
            "confidence": self.confidence,
            "risk": self.risk,
            "policy_checks": self.policy_checks,
            "recommendation_id": str(self.recommendation_id),
            "evidence": self.evidence,
            "autonomy_mode": self.autonomy_mode,
            "proposed": (
                {
                    "action_type": self.proposed.action_type.value,
                    "direction": self.proposed.direction,
                    "percentage": self.proposed.percentage,
                    "daily_budget": float(self.proposed.daily_budget)
                    if self.proposed and self.proposed.daily_budget is not None
                    else None,
                    "risk_level": self.proposed.risk_level.value
                    if self.proposed and hasattr(self.proposed.risk_level, "value")
                    else None,
                    "payload": self.proposed.payload if self.proposed else {},
                }
                if self.proposed
                else None
            ),
        }


def map_recommendation_to_proposal(
    recommendation: PerformanceRecommendation,
    *,
    current_daily_budget: Decimal | None,
) -> tuple[ProposedAction | None, str | None]:
    """
    Evidence-bound mapping from suggested_action → AIActionType.

    Returns (proposal, skip_reason). Unsupported/non-executable ops → NO_ACTION.
    """
    suggested = recommendation.suggested_action or {}
    operation = str(suggested.get("operation") or "").upper()
    direction = suggested.get("direction")
    percentage = suggested.get("percentage")
    try:
        pct = float(percentage) if percentage is not None else None
    except (TypeError, ValueError):
        pct = None

    action_type = EXECUTABLE_OPERATIONS.get(operation)
    if action_type is None:
        return None, f"Operation {operation or 'UNKNOWN'} is not an executable ads mutation"

    daily_budget = None
    payload: dict[str, Any] = {
        "recommendation_id": str(recommendation.id),
        "fingerprint": recommendation.fingerprint,
        "signal_category": recommendation.signal_category,
        "informational_source": True,
    }

    if action_type == AIActionType.update_budget:
        if current_daily_budget is None or current_daily_budget <= 0:
            return None, "Current campaign daily_budget unknown — cannot compute UPDATE_BUDGET"
        if pct is None or pct <= 0:
            return None, "Budget change percentage missing or invalid"
        direction_u = str(direction or "").upper()
        if direction_u == "DECREASE":
            daily_budget = (current_daily_budget * (Decimal("1") - Decimal(str(pct)) / Decimal("100"))).quantize(
                Decimal("0.01")
            )
        elif direction_u == "INCREASE":
            daily_budget = (current_daily_budget * (Decimal("1") + Decimal(str(pct)) / Decimal("100"))).quantize(
                Decimal("0.01")
            )
        else:
            return None, f"Unsupported budget direction {direction!r}"
        payload["daily_budget"] = float(daily_budget)
        payload["budget_change_percent"] = pct
        payload["budget_direction"] = direction_u
        payload["previous_daily_budget"] = float(current_daily_budget)

    risk = classify_optimization_risk(action_type=action_type, budget_change_percent=pct)
    return (
        ProposedAction(
            action_type=action_type,
            direction=str(direction) if direction else None,
            percentage=pct,
            daily_budget=daily_budget,
            risk_level=risk,
            payload=payload,
        ),
        None,
    )


def build_evidence_snapshot(recommendation: PerformanceRecommendation) -> dict[str, Any]:
    return {
        "recommendation_type": recommendation.recommendation_type,
        "severity": recommendation.severity,
        "confidence": float(recommendation.confidence or 0),
        "platform": recommendation.platform,
        "external_campaign_id": recommendation.external_campaign_id,
        "evidence": recommendation.evidence or [],
        "current_values": recommendation.current_values or {},
        "comparison_values": recommendation.comparison_values or {},
        "percentage_changes": recommendation.percentage_changes or {},
        "window_days": recommendation.analysis_window_days,
        "suggested_action": recommendation.suggested_action or {},
    }
