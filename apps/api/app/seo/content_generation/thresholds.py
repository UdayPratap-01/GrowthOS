"""SEO content generation thresholds and versions (M9.9)."""

from __future__ import annotations

from dataclasses import dataclass


ALGORITHM_VERSION = "seo_content_generation_v1"
PROMPT_VERSION = "seo_content_generation_prompt_v1"

GENERATABLE_BRIEF_STATUSES = frozenset({"ready", "draft"})

CONTENT_TYPES = frozenset({
    "informational_article",
    "blog_article",
    "guide",
    "comparison",
    "service_content",
    "landing_page_draft",
    "content_refresh",
})

CONTENT_STATUSES = frozenset({"draft", "ready", "archived"})

HEADING_LEVELS = frozenset({"H2", "H3"})

UNAVAILABLE_KEYWORD = "unavailable"

FABRICATION_PATTERNS = (
    "search volume of",
    "ranks #1",
    "rank #1",
    "monthly traffic of",
    "backlinks from",
    "verified testimonial",
    "award-winning",
    "certified by google",
)


@dataclass(frozen=True)
class SeoContentGenerationLimits:
    max_prompt_chars: int = 120_000
    max_output_chars: int = 50_000
    max_sections: int = 15
    max_subsections_per_section: int = 5
    max_internal_links: int = 20
    meta_title_max: int = 70
    meta_description_max: int = 160


DEFAULT_SEO_CONTENT_GENERATION_LIMITS = SeoContentGenerationLimits()
