"""Safe structured optimization rule evaluation — no arbitrary code execution."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

ALLOWED_METRICS = frozenset(
    {"cpl", "ctr", "spend", "leads", "conversions", "roas", "frequency", "impressions", "clicks"}
)
ALLOWED_OPERATORS = frozenset({"gt", "gte", "lt", "lte", "eq"})


@dataclass
class RuleMatch:
    matched: bool
    reason: str = ""
    metric: str | None = None
    observed: float | None = None
    threshold: float | None = None


def _coerce_number(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(Decimal(str(value)))
    except Exception:
        return None


def evaluate_condition(condition: dict, metrics: dict[str, Any]) -> RuleMatch:
    """
    Evaluate a single structured condition:
    {"metric": "cpl", "operator": "gt", "value": 50}
    """
    metric = str((condition or {}).get("metric") or "").strip().lower()
    operator = str((condition or {}).get("operator") or "").strip().lower()
    threshold = _coerce_number((condition or {}).get("value"))

    if metric not in ALLOWED_METRICS:
        return RuleMatch(matched=False, reason=f"UNSUPPORTED_METRIC:{metric or 'missing'}")
    if operator not in ALLOWED_OPERATORS:
        return RuleMatch(matched=False, reason=f"UNSUPPORTED_OPERATOR:{operator or 'missing'}")
    if threshold is None:
        return RuleMatch(matched=False, reason="INVALID_THRESHOLD")

    observed = _coerce_number(metrics.get(metric))
    if observed is None:
        return RuleMatch(matched=False, reason=f"INSUFFICIENT_DATA:{metric}")

    matched = False
    if operator == "gt":
        matched = observed > threshold
    elif operator == "gte":
        matched = observed >= threshold
    elif operator == "lt":
        matched = observed < threshold
    elif operator == "lte":
        matched = observed <= threshold
    elif operator == "eq":
        matched = observed == threshold

    reason = f"{metric} {operator} {threshold} (observed={observed})"
    return RuleMatch(matched=matched, reason=reason, metric=metric, observed=observed, threshold=threshold)


def evaluate_rule_conditions(conditions: list[dict] | dict, metrics: dict[str, Any]) -> RuleMatch:
    """All conditions must match (AND). Legacy dict conditions are wrapped as a single rule."""
    if isinstance(conditions, dict):
        # Backward compatible CPL rule shape from older OptimizationRule rows.
        if "target_cpl" in conditions:
            target = _coerce_number(conditions.get("target_cpl"))
            multiplier = _coerce_number(conditions.get("cpl_multiplier")) or 1.3
            min_spend = _coerce_number(conditions.get("minimum_spend")) or 0
            min_conv = int(conditions.get("minimum_conversions") or 0)
            cpl = _coerce_number(metrics.get("cpl"))
            spend = _coerce_number(metrics.get("spend")) or 0
            conversions = int(metrics.get("conversions") or metrics.get("leads") or 0)
            if target is None or cpl is None:
                return RuleMatch(matched=False, reason="INSUFFICIENT_DATA:cpl")
            if cpl > target * multiplier and spend > min_spend and conversions >= min_conv:
                return RuleMatch(
                    matched=True,
                    reason=f"CPL {cpl} > target {target} × {multiplier}",
                    metric="cpl",
                    observed=cpl,
                    threshold=target * multiplier,
                )
            return RuleMatch(matched=False, reason="legacy_cpl_rule_not_met")
        conditions = [conditions]

    if not conditions:
        return RuleMatch(matched=False, reason="NO_CONDITIONS")

    for cond in conditions:
        result = evaluate_condition(cond, metrics)
        if not result.matched:
            return result
    return RuleMatch(matched=True, reason="all_conditions_met")
