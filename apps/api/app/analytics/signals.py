"""Deterministic performance signal detection from period comparisons."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.analytics.windows import EntityPeriodComparison
from app.core.config import Settings, get_settings


@dataclass
class PerformanceSignal:
    category: str  # UNDERPERFORMANCE | POSITIVE | EFFICIENCY | TREND
    recommendation_type: str
    severity: str  # LOW | MEDIUM | HIGH
    title: str
    metric: str
    current: float | None
    previous: float | None
    change_percent: float | None
    suggested_action: dict[str, Any]
    evidence: list[dict[str, Any]] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)


def _meets_sample(comparison: EntityPeriodComparison, settings: Settings) -> tuple[bool, list[str]]:
    reasons: list[str] = []
    cur = comparison.current
    # Require either current or previous period to have meaningful activity,
    # and both periods to have at least one day of data for a fair compare.
    if cur.days_with_data < max(1, settings.performance_min_days_with_data):
        reasons.append("insufficient_current_days")
    if comparison.previous.days_with_data < max(1, settings.performance_min_days_with_data):
        reasons.append("insufficient_previous_days")
    spend = float(cur.spend)
    impressions = cur.impressions
    clicks = cur.clicks
    conversions = float(cur.conversions)
    if spend < settings.performance_min_spend and impressions < settings.performance_min_impressions:
        reasons.append("below_min_spend_and_impressions")
    if clicks < settings.performance_min_clicks and conversions < settings.performance_min_conversions:
        # Still allow spend/impression efficiency signals without clicks.
        if spend < settings.performance_min_spend:
            reasons.append("below_min_clicks_and_conversions")
    return (len(reasons) == 0), reasons


def _sig_change(change: float | None, settings: Settings) -> bool:
    if change is None:
        return False
    return abs(change) >= settings.performance_significant_change_percent


def detect_signals(
    comparison: EntityPeriodComparison,
    *,
    settings: Settings | None = None,
    account_avg_cpl: float | None = None,
    account_avg_roas: float | None = None,
) -> list[PerformanceSignal]:
    """
    Emit structured signals only when change magnitude clears thresholds and
    sample size is adequate. Slight noise does not produce recommendations.
    """
    settings = settings or get_settings()
    ok, reasons = _meets_sample(comparison, settings)
    comparison.insufficient_data = not ok
    comparison.insufficient_reasons = reasons
    if not ok:
        return []

    cur = comparison.current.as_dict()
    prev = comparison.previous.as_dict()
    changes = comparison.percentage_changes
    threshold = settings.performance_significant_change_percent
    signals: list[PerformanceSignal] = []

    def add(
        *,
        category: str,
        recommendation_type: str,
        severity: str,
        title: str,
        metric: str,
        suggested_action: dict[str, Any],
        extra_evidence: list[dict[str, Any]] | None = None,
    ) -> None:
        change = changes.get(metric)
        evidence = [
            {
                "metric": metric,
                "current": cur.get(metric),
                "previous": prev.get(metric),
                "change_percent": round(change, 4) if change is not None else None,
            }
        ]
        if extra_evidence:
            evidence.extend(extra_evidence)
        signals.append(
            PerformanceSignal(
                category=category,
                recommendation_type=recommendation_type,
                severity=severity,
                title=title,
                metric=metric,
                current=cur.get(metric),
                previous=prev.get(metric),
                change_percent=change,
                suggested_action=suggested_action,
                evidence=evidence,
            )
        )

    # --- UNDERPERFORMANCE ---
    if _sig_change(changes.get("cpl"), settings) and (changes.get("cpl") or 0) > 0 and cur.get("cpl") is not None:
        add(
            category="UNDERPERFORMANCE",
            recommendation_type="REDUCE_BUDGET",
            severity="HIGH" if abs(changes["cpl"]) >= threshold * 1.5 else "MEDIUM",
            title="Campaign CPL increased significantly",
            metric="cpl",
            suggested_action={
                "operation": "UPDATE_BUDGET",
                "direction": "DECREASE",
                "percentage": 15,
                "informational_only": True,
            },
        )
    if _sig_change(changes.get("cpa"), settings) and (changes.get("cpa") or 0) > 0 and cur.get("cpa") is not None:
        add(
            category="UNDERPERFORMANCE",
            recommendation_type="REDUCE_BUDGET",
            severity="HIGH" if abs(changes["cpa"]) >= threshold * 1.5 else "MEDIUM",
            title="Campaign CPA increased significantly",
            metric="cpa",
            suggested_action={
                "operation": "UPDATE_BUDGET",
                "direction": "DECREASE",
                "percentage": 15,
                "informational_only": True,
            },
        )
    if _sig_change(changes.get("roas"), settings) and (changes.get("roas") or 0) < 0 and prev.get("roas"):
        add(
            category="UNDERPERFORMANCE",
            recommendation_type="REVIEW_CREATIVE",
            severity="HIGH" if abs(changes["roas"]) >= threshold * 1.5 else "MEDIUM",
            title="Campaign ROAS decreased significantly",
            metric="roas",
            suggested_action={
                "operation": "REVIEW_PERFORMANCE",
                "direction": "INVESTIGATE",
                "informational_only": True,
            },
        )
    if _sig_change(changes.get("ctr"), settings) and (changes.get("ctr") or 0) < 0 and prev.get("ctr"):
        add(
            category="UNDERPERFORMANCE",
            recommendation_type="REFRESH_CREATIVE",
            severity="MEDIUM",
            title="Campaign CTR decreased significantly",
            metric="ctr",
            suggested_action={
                "operation": "CREATE_CREATIVE",
                "direction": "REFRESH",
                "informational_only": True,
            },
        )
    if _sig_change(changes.get("cpc"), settings) and (changes.get("cpc") or 0) > 0 and cur.get("cpc") is not None:
        add(
            category="UNDERPERFORMANCE",
            recommendation_type="REVIEW_BIDDING",
            severity="MEDIUM",
            title="Campaign CPC increased significantly",
            metric="cpc",
            suggested_action={
                "operation": "REVIEW_PERFORMANCE",
                "direction": "INVESTIGATE",
                "informational_only": True,
            },
        )
    if _sig_change(changes.get("conversions"), settings) and (changes.get("conversions") or 0) < 0:
        add(
            category="UNDERPERFORMANCE",
            recommendation_type="REVIEW_FUNNEL",
            severity="MEDIUM",
            title="Campaign conversions decreased significantly",
            metric="conversions",
            suggested_action={
                "operation": "REVIEW_PERFORMANCE",
                "direction": "INVESTIGATE",
                "informational_only": True,
            },
        )

    # --- POSITIVE ---
    if _sig_change(changes.get("roas"), settings) and (changes.get("roas") or 0) > 0 and cur.get("roas") is not None:
        add(
            category="POSITIVE",
            recommendation_type="SCALE_BUDGET",
            severity="MEDIUM",
            title="Campaign ROAS improved significantly",
            metric="roas",
            suggested_action={
                "operation": "UPDATE_BUDGET",
                "direction": "INCREASE",
                "percentage": 10,
                "informational_only": True,
            },
        )
    if _sig_change(changes.get("cpl"), settings) and (changes.get("cpl") or 0) < 0 and cur.get("cpl") is not None:
        add(
            category="POSITIVE",
            recommendation_type="SCALE_BUDGET",
            severity="MEDIUM",
            title="Campaign CPL improved significantly",
            metric="cpl",
            suggested_action={
                "operation": "UPDATE_BUDGET",
                "direction": "INCREASE",
                "percentage": 10,
                "informational_only": True,
            },
        )
    if _sig_change(changes.get("ctr"), settings) and (changes.get("ctr") or 0) > 0:
        add(
            category="POSITIVE",
            recommendation_type="SCALE_WINNING_CREATIVE",
            severity="LOW",
            title="Campaign CTR improved significantly",
            metric="ctr",
            suggested_action={
                "operation": "REVIEW_PERFORMANCE",
                "direction": "SCALE",
                "informational_only": True,
            },
        )
    if _sig_change(changes.get("conversions"), settings) and (changes.get("conversions") or 0) > 0:
        add(
            category="POSITIVE",
            recommendation_type="SCALE_BUDGET",
            severity="LOW",
            title="Campaign conversions increased significantly",
            metric="conversions",
            suggested_action={
                "operation": "UPDATE_BUDGET",
                "direction": "INCREASE",
                "percentage": 10,
                "informational_only": True,
            },
        )

    # --- EFFICIENCY vs account average ---
    if (
        account_avg_cpl is not None
        and cur.get("cpl") is not None
        and float(cur["spend"]) >= settings.performance_min_spend
        and float(cur["cpl"]) > account_avg_cpl * (1 + threshold / 100.0)
    ):
        add(
            category="EFFICIENCY",
            recommendation_type="REDUCE_BUDGET",
            severity="HIGH",
            title="High spend with poor conversion efficiency vs account",
            metric="cpl",
            suggested_action={
                "operation": "UPDATE_BUDGET",
                "direction": "DECREASE",
                "percentage": 20,
                "informational_only": True,
            },
            extra_evidence=[
                {
                    "metric": "account_avg_cpl",
                    "current": account_avg_cpl,
                    "previous": None,
                    "change_percent": None,
                },
                {
                    "metric": "spend",
                    "current": cur.get("spend"),
                    "previous": prev.get("spend"),
                    "change_percent": changes.get("spend"),
                },
            ],
        )
    if (
        account_avg_roas is not None
        and cur.get("roas") is not None
        and float(cur["spend"]) > 0
        and float(cur["spend"]) < settings.performance_min_spend * 2
        and float(cur["roas"]) > account_avg_roas * (1 + threshold / 100.0)
    ):
        add(
            category="EFFICIENCY",
            recommendation_type="SCALE_BUDGET",
            severity="MEDIUM",
            title="Low spend with unusually strong ROAS vs account",
            metric="roas",
            suggested_action={
                "operation": "UPDATE_BUDGET",
                "direction": "INCREASE",
                "percentage": 15,
                "informational_only": True,
            },
            extra_evidence=[
                {
                    "metric": "account_avg_roas",
                    "current": account_avg_roas,
                    "previous": None,
                    "change_percent": None,
                }
            ],
        )

    # --- TREND: sudden large swing on spend-normalized outcomes ---
    sudden = settings.performance_sudden_change_percent
    for metric in ("cpl", "roas", "ctr", "conversions"):
        change = changes.get(metric)
        if change is None:
            continue
        if abs(change) >= sudden:
            direction = "deterioration" if (
                (metric in {"cpl", "cpa", "cpc"} and change > 0)
                or (metric in {"roas", "ctr", "conversions"} and change < 0)
            ) else "improvement"
            add(
                category="TREND",
                recommendation_type="INVESTIGATE_ANOMALY",
                severity="HIGH",
                title=f"Sudden {metric.upper()} {direction}",
                metric=metric,
                suggested_action={
                    "operation": "REVIEW_PERFORMANCE",
                    "direction": "INVESTIGATE",
                    "informational_only": True,
                },
            )

    # Deduplicate by recommendation_type + metric (keep highest severity)
    severity_rank = {"LOW": 1, "MEDIUM": 2, "HIGH": 3}
    best: dict[tuple[str, str], PerformanceSignal] = {}
    for sig in signals:
        key = (sig.recommendation_type, sig.metric)
        existing = best.get(key)
        if existing is None or severity_rank[sig.severity] > severity_rank[existing.severity]:
            best[key] = sig
    return list(best.values())
