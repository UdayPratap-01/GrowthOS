"""Deterministic keyword opportunity thresholds."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class KeywordOpportunityThresholds:
    min_impressions: int = 100
    strong_impressions: int = 500
    low_ctr: float = 0.02
    near_zero_clicks_max: int = 2
    page_one_max_position: float = 10.0
    page_two_min_position: float = 10.0
    page_two_max_position: float = 20.0
    near_page_one_min: float = 8.0
    near_page_one_max: float = 15.0
    strong_position_max: float = 10.0
    growth_pct: float = 20.0
    decline_pct: float = -20.0
    long_tail_min_tokens: int = 4
    multi_page_min_pages: int = 2


DEFAULT_KEYWORD_THRESHOLDS = KeywordOpportunityThresholds()
