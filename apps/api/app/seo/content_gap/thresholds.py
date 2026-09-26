"""Deterministic content-gap thresholds (competitor_gap_v1)."""

from __future__ import annotations

from dataclasses import dataclass


ALGORITHM_VERSION = "competitor_gap_v1"

STRONG_MATCH = 0.45
MODERATE_MATCH = 0.25
WEAK_MATCH = 0.15

GAP_TYPES = (
    "COMPETITOR_TOPIC_NO_USER_CLUSTER",
    "COMPETITOR_PAGE_WEAK_USER_COVERAGE",
    "USER_OPPORTUNITY_NO_STRONG_PAGE",
    "MULTI_COMPETITOR_LIMITED_USER",
    "USER_TOPIC_COMPETITOR_DEPTH",
    "COMPETITOR_LEXICAL_NO_USER_TOPIC",
)


@dataclass(frozen=True)
class ContentGapThresholds:
    strong_match: float = STRONG_MATCH
    moderate_match: float = MODERATE_MATCH
    weak_match: float = WEAK_MATCH
    max_comparisons: int = 5000
    weak_user_page_count: int = 1
    weak_user_impressions: float = 100.0


DEFAULT_GAP_THRESHOLDS = ContentGapThresholds()
