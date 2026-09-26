"""Deterministic Search Console opportunity heuristics."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class GscIntelligenceThresholds:
    min_impressions: int = 100
    low_ctr: float = 0.02
    strong_impressions: int = 500
    page_boundary_min: float = 8.0
    page_boundary_max: float = 15.0
    strong_position_max: float = 10.0
    declining_pct: float = -20.0
    min_clicks_for_decline: int = 5


DEFAULT_GSC_THRESHOLDS = GscIntelligenceThresholds()
