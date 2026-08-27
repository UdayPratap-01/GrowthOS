"""Risk classification for optimization-proposed actions."""

from __future__ import annotations

from decimal import Decimal

from app.models.enums import AIActionType, RiskLevel


def classify_optimization_risk(
    *,
    action_type: AIActionType,
    budget_change_percent: float | None = None,
) -> RiskLevel:
    """
    HIGH-risk actions must never become autonomous solely due to high confidence.

    - pause_campaign → HIGH
    - update_budget with |change| > 20% → HIGH
    - update_budget with |change| > 10% → MEDIUM
    - update_budget small → LOW
    - resume → MEDIUM
    """
    if action_type == AIActionType.pause_campaign:
        return RiskLevel.high
    if action_type == AIActionType.resume_campaign:
        return RiskLevel.medium
    if action_type == AIActionType.update_budget:
        pct = abs(float(budget_change_percent or 0))
        if pct > 20:
            return RiskLevel.high
        if pct > 10:
            return RiskLevel.medium
        return RiskLevel.low
    # Default conservative
    return RiskLevel.high


def risk_allows_autonomous(risk: RiskLevel, *, max_autonomous_risk: str) -> bool:
    order = {RiskLevel.low: 1, RiskLevel.medium: 2, RiskLevel.high: 3}
    try:
        cap = RiskLevel(max_autonomous_risk.strip().lower())
    except ValueError:
        cap = RiskLevel.low
    return order[risk] <= order[cap]
