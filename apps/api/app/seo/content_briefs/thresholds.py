"""SEO content brief thresholds and versions (M9.8)."""

from __future__ import annotations

from dataclasses import dataclass


ALGORITHM_VERSION = "seo_content_brief_v1"
PROMPT_VERSION = "seo_content_brief_prompt_v1"

BRIEF_ELIGIBLE_RECOMMENDATION_TYPES = frozenset({
    "content_refresh",
    "keyword_targeting",
    "topic_expansion",
    "content_gap",
    "search_intent",
    "competitor_gap",
})

BRIEF_TYPES = frozenset({
    "content_refresh",
    "keyword_targeting",
    "topic_expansion",
    "content_gap",
    "search_intent",
    "competitor_gap",
})

BRIEF_STATUSES = frozenset({"draft", "ready", "archived"})

SEARCH_INTENT_TYPES = frozenset({
    "informational",
    "commercial",
    "transactional",
    "navigational",
    "mixed",
    "unknown",
})

OUTLINE_LEVELS = frozenset({"H2", "H3"})

EVIDENCE_SOURCES = frozenset({
    "seo_finding",
    "keyword_opportunity",
    "topic_cluster",
    "content_gap",
    "search_console_opportunity",
    "competitor_page",
})

UNAVAILABLE_PRIMARY_KEYWORD = "unavailable"
UNAVAILABLE_TARGET_AUDIENCE = "unavailable"

UNAVAILABLE_COMPETITOR_FIELDS = {
    "competitor_rankings": "unavailable",
    "competitor_search_volume": "unavailable",
    "competitor_traffic": "unavailable",
    "competitor_backlinks": "unavailable",
}


@dataclass(frozen=True)
class SeoContentBriefLimits:
    max_evidence_records: int = 15
    max_internal_links: int = 20
    max_secondary_keywords: int = 15
    max_outline_sections: int = 12
    max_questions: int = 12
    max_entities: int = 20
    max_content_requirements: int = 15
    max_seo_requirements: int = 15
    max_prompt_chars: int = 100_000


DEFAULT_SEO_CONTENT_BRIEF_LIMITS = SeoContentBriefLimits()
