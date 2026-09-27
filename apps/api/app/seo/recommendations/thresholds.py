"""SEO AI recommendation thresholds and versions (M9.7)."""

from __future__ import annotations

from dataclasses import dataclass


ALGORITHM_VERSION = "seo_recommendation_v1"
PROMPT_VERSION = "seo_recommendation_prompt_v1"

RECOMMENDATION_TYPES = frozenset({
    "technical_seo",
    "content_refresh",
    "keyword_targeting",
    "topic_expansion",
    "content_gap",
    "metadata_optimization",
    "page_structure",
    "search_intent",
    "competitor_gap",
})

PRIORITY_VALUES = frozenset({"high", "medium", "low"})
IMPACT_VALUES = frozenset({"high", "medium", "low"})
EFFORT_VALUES = frozenset({"high", "medium", "low"})

EVIDENCE_SOURCES = frozenset({
    "seo_finding",
    "keyword_opportunity",
    "topic_cluster",
    "content_gap",
    "search_console_opportunity",
    "competitor_page",
})

UNAVAILABLE_COMPETITOR_FIELDS = {
    "competitor_rankings": "unavailable",
    "competitor_search_volume": "unavailable",
    "competitor_traffic": "unavailable",
    "competitor_backlinks": "unavailable",
}


@dataclass(frozen=True)
class SeoRecommendationLimits:
    max_findings: int = 20
    max_keywords: int = 30
    max_topics: int = 20
    max_gaps: int = 20
    max_competitor_pages: int = 15
    max_recommendations_per_run: int = 15
    max_prompt_chars: int = 120_000


DEFAULT_SEO_RECOMMENDATION_LIMITS = SeoRecommendationLimits()
