"""AI explanation layer — deterministic evidence is the source of truth."""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from app.ai.providers.base import Message
from app.ai.providers.factory import get_ai_provider
from app.analytics.signals import PerformanceSignal
from app.analytics.windows import EntityPeriodComparison

logger = logging.getLogger(__name__)


def deterministic_explanation(
    comparison: EntityPeriodComparison,
    signal: PerformanceSignal,
) -> str:
    entity = comparison.external_campaign_id or comparison.external_account_id or "entity"
    change = signal.change_percent
    change_txt = f"{change:+.1f}%" if change is not None else "n/a"
    current = signal.current
    previous = signal.previous
    return (
        f"{signal.title} for {comparison.platform} {comparison.entity_level} "
        f"{entity} over the last {comparison.window.days} days versus the prior "
        f"{comparison.window.days} days. {signal.metric.upper()} moved from "
        f"{previous} to {current} ({change_txt}). "
        f"Suggested action is informational only and will not execute automatically: "
        f"{signal.suggested_action.get('operation')} "
        f"({signal.suggested_action.get('direction')})."
    )


def _extract_numbers(text: str) -> list[float]:
    found: list[float] = []
    for match in re.findall(r"-?\d+(?:\.\d+)?", text or ""):
        try:
            found.append(float(match))
        except ValueError:
            continue
    return found


def _allowed_numbers(signal: PerformanceSignal, comparison: EntityPeriodComparison) -> set[float]:
    allowed: set[float] = set()
    for value in (
        signal.current,
        signal.previous,
        signal.change_percent,
        comparison.window.days,
        float(comparison.current.spend),
        float(comparison.previous.spend),
        comparison.current.impressions,
        comparison.previous.impressions,
        comparison.current.clicks,
        comparison.previous.clicks,
        float(comparison.current.conversions),
        float(comparison.previous.conversions),
        comparison.current.leads,
        comparison.previous.leads,
        float(comparison.current.revenue),
        float(comparison.previous.revenue),
    ):
        if value is None:
            continue
        allowed.add(round(float(value), 4))
        allowed.add(round(float(value), 2))
        allowed.add(round(float(value), 1))
        allowed.add(float(int(float(value))))
    for item in signal.evidence:
        for key in ("current", "previous", "change_percent"):
            if item.get(key) is None:
                continue
            allowed.add(round(float(item[key]), 4))
            allowed.add(round(float(item[key]), 2))
            allowed.add(round(float(item[key]), 1))
    # Common harmless numbers in prose
    allowed.update({0.0, 1.0, 2.0, 5.0, 7.0, 10.0, 14.0, 15.0, 20.0, 30.0, 100.0})
    return allowed


def reject_hallucinated_metrics(
    text: str,
    signal: PerformanceSignal,
    comparison: EntityPeriodComparison,
) -> bool:
    """
    Return True when the explanation appears to invent numeric claims outside
    the evidence set. Conservative: large unexplained numbers fail the check.
    """
    allowed = _allowed_numbers(signal, comparison)
    for number in _extract_numbers(text):
        rounded = {round(number, 4), round(number, 2), round(number, 1), float(int(number))}
        if not rounded.intersection(allowed):
            # Allow small integers used as counts of bullets/sentences
            if abs(number) <= 3 and float(number).is_integer():
                continue
            return True
    return False


async def explain_recommendation(
    comparison: EntityPeriodComparison,
    signal: PerformanceSignal,
    *,
    confidence: float,
) -> tuple[str, str]:
    """
    Returns (explanation, source) where source is 'ai' or 'deterministic'.

    On any AI failure or hallucinated metrics, falls back to deterministic text.
    """
    fallback = deterministic_explanation(comparison, signal)
    evidence_payload = {
        "title": signal.title,
        "category": signal.category,
        "metric": signal.metric,
        "platform": comparison.platform,
        "entity_level": comparison.entity_level,
        "external_campaign_id": comparison.external_campaign_id,
        "window_days": comparison.window.days,
        "current": signal.current,
        "previous": signal.previous,
        "change_percent": signal.change_percent,
        "evidence": signal.evidence,
        "confidence": confidence,
        "suggested_action": signal.suggested_action,
    }
    prompt = (
        "You are explaining a marketing performance recommendation. "
        "Use ONLY the numeric values provided in the JSON evidence. "
        "Do not invent metrics, spend figures, or percentages. "
        "Write 2-4 concise sentences covering: what changed, business interpretation, "
        "and why the suggested action is reasonable. "
        "Remind the reader that the action is informational and not executed.\n\n"
        f"EVIDENCE_JSON:\n{json.dumps(evidence_payload, default=str)}"
    )
    try:
        provider = get_ai_provider()
        response = await provider.complete(
            [
                Message(role="system", content="You explain marketing analytics without inventing numbers."),
                Message(role="user", content=prompt),
            ]
        )
        text = (getattr(response, "content", None) or "").strip()
        if not text:
            return fallback, "deterministic"
        if reject_hallucinated_metrics(text, signal, comparison):
            logger.warning(
                "Rejected AI explanation with numbers outside evidence entity=%s metric=%s",
                comparison.external_campaign_id,
                signal.metric,
            )
            return fallback, "deterministic"
        return text, "ai"
    except Exception as exc:
        logger.info("AI explanation unavailable; using deterministic fallback: %s", str(exc)[:200])
        return fallback, "deterministic"
