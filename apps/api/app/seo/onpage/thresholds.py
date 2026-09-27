"""SEO on-page optimizer thresholds and versions (M9.10)."""

from __future__ import annotations

from dataclasses import dataclass


ALGORITHM_VERSION = "seo_onpage_optimizer_v1"
PROMPT_VERSION = "seo_onpage_optimizer_prompt_v1"

FINDING_CATEGORIES = frozenset({
    "title",
    "metadata",
    "headings",
    "keyword",
    "topic",
    "search_intent",
    "content_structure",
    "readability",
    "internal_links",
    "content_depth",
    "questions",
    "entities",
    "duplicate_content",
})

SEVERITIES = frozenset({"critical", "high", "medium", "low", "info"})
PRIORITIES = frozenset({"high", "medium", "low"})
FINDING_STATUSES = frozenset({"open", "resolved", "dismissed", "not_evaluable"})

EVIDENCE_SOURCES = frozenset({
    "generated_content",
    "content_brief",
    "crawl_observation",
    "seo_finding",
    "search_console",
    "keyword_opportunity",
    "topic_cluster",
    "content_gap",
})

UNAVAILABLE_KEYWORD = "unavailable"

META_TITLE_MAX = 70
META_DESC_MAX = 160
META_TITLE_MIN = 30
KEYWORD_STUFFING_RATIO = 0.03
MIN_WORD_COUNT_GUIDE = 200
LONG_PARAGRAPH_WORDS = 150


@dataclass(frozen=True)
class SeoOnPageLimits:
    max_findings: int = 100
    max_ai_suggestions: int = 10
    max_prompt_chars: int = 80_000
    optimize_rate_per_hour: int = 24


DEFAULT_ONPAGE_LIMITS = SeoOnPageLimits()
