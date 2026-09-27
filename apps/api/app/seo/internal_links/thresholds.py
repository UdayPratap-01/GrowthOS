"""Thresholds and limits for the internal-link engine (M9.12)."""

from __future__ import annotations

from dataclasses import dataclass

ALGORITHM_VERSION = "internal_link_engine_v1"
PROMPT_VERSION = "internal_link_prompt_v1"

OPPORTUNITY_TYPES = frozenset(
    {
        "related_content",
        "topic_support",
        "keyword_support",
        "orphan_page",
        "deep_page",
        "hub_to_detail",
        "detail_to_hub",
        "complementary_content",
        "contextual_support",
        "content_cluster_navigation",
    }
)

CONFIDENCE_LEVELS = frozenset({"low", "medium", "high"})

OPPORTUNITY_STATUSES = frozenset({"suggested", "accepted", "rejected", "superseded"})


@dataclass(frozen=True)
class InternalLinkLimits:
    max_source_pages: int = 50
    max_target_candidates: int = 200
    max_opportunities: int = 75
    max_keyword_comparisons: int = 500
    max_topic_comparisons: int = 200
    max_ai_suggestions: int = 20
    max_prompt_chars: int = 80_000
    min_relevance_score: float = 0.35
    orphan_max_depth: int = 8
    deep_page_min_depth: int = 4


DEFAULT_INTERNAL_LINK_LIMITS = InternalLinkLimits()
