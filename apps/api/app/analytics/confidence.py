"""Transparent confidence scoring for performance recommendations.

This is NOT statistical significance testing. It is a heuristic score in [0, 1]
that reflects sample size, magnitude of change, data completeness, and how
reliable the underlying metric tends to be given volume.
"""

from __future__ import annotations

from decimal import Decimal

from app.analytics.signals import PerformanceSignal
from app.analytics.windows import EntityPeriodComparison
from app.core.config import Settings, get_settings


def score_confidence(
    comparison: EntityPeriodComparison,
    signal: PerformanceSignal,
    *,
    settings: Settings | None = None,
) -> Decimal:
    settings = settings or get_settings()
    score = 0.35

    # Sample size — days with data in both windows
    days = min(comparison.current.days_with_data, comparison.previous.days_with_data)
    expected = comparison.window.days
    completeness = days / expected if expected else 0
    score += 0.25 * min(1.0, completeness)

    # Volume reliability
    impressions = comparison.current.impressions
    clicks = comparison.current.clicks
    conversions = float(comparison.current.conversions)
    spend = float(comparison.current.spend)
    volume_score = 0.0
    if impressions >= settings.performance_min_impressions * 2:
        volume_score += 0.08
    elif impressions >= settings.performance_min_impressions:
        volume_score += 0.04
    if clicks >= settings.performance_min_clicks * 2:
        volume_score += 0.08
    elif clicks >= settings.performance_min_clicks:
        volume_score += 0.04
    if conversions >= settings.performance_min_conversions * 2:
        volume_score += 0.08
    elif conversions >= settings.performance_min_conversions:
        volume_score += 0.04
    if spend >= settings.performance_min_spend * 2:
        volume_score += 0.06
    elif spend >= settings.performance_min_spend:
        volume_score += 0.03
    score += min(0.30, volume_score)

    # Magnitude of change relative to significance threshold
    change = abs(signal.change_percent or 0)
    threshold = settings.performance_significant_change_percent
    if threshold > 0:
        magnitude = min(2.0, change / threshold) / 2.0
        score += 0.20 * magnitude

    # Metric reliability bias: rates need more volume than raw counts
    if signal.metric in {"ctr", "cpc", "cpm", "cpl", "cpa", "roas"} and clicks < settings.performance_min_clicks:
        score -= 0.10
    if signal.metric in {"cpl", "cpa", "roas"} and conversions < settings.performance_min_conversions:
        score -= 0.08

    # Clamp
    score = max(0.05, min(0.99, score))
    return Decimal(str(round(score, 4)))
