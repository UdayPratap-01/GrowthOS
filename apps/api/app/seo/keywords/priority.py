"""Deterministic keyword opportunity priority — not a prediction score."""

from __future__ import annotations

import math


def compute_priority_score(*, impressions: float, ctr: float, average_position: float, low_ctr: float) -> tuple[str, float]:
    """
    Priority formula (documented, deterministic):
    score = log10(impressions+1) * position_factor * ctr_gap_factor
    position_factor = max(0.5, (21 - avg_position) / 20)
    ctr_gap_factor = max(1.0, (low_ctr - ctr) / low_ctr) when ctr < low_ctr else 1.0
    """
    position_factor = max(0.5, (21.0 - average_position) / 20.0)
    ctr_gap_factor = max(1.0, (low_ctr - ctr) / low_ctr) if ctr < low_ctr and low_ctr > 0 else 1.0
    score = math.log10(impressions + 1) * position_factor * ctr_gap_factor

    if score >= 4.0:
        priority = "high"
    elif score >= 2.5:
        priority = "medium"
    elif score >= 1.0:
        priority = "low"
    else:
        priority = "info"
    return priority, round(score, 4)
