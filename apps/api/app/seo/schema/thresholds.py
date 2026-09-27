"""SEO schema generator/validator thresholds (M9.11)."""

from __future__ import annotations

from dataclasses import dataclass


GENERATION_ALGORITHM = "seo_schema_generation_v1"
VALIDATION_ALGORITHM = "seo_schema_validation_v1"
PROMPT_VERSION = "seo_schema_prompt_v1"

SCHEMA_CONTEXT = "https://schema.org"

SUPPORTED_SCHEMA_TYPES = frozenset({
    "Article",
    "BlogPosting",
    "NewsArticle",
    "WebPage",
    "FAQPage",
    "HowTo",
    "Service",
    "Product",
    "LocalBusiness",
    "Organization",
    "BreadcrumbList",
})

ELIGIBILITY_STATUSES = frozenset({"eligible", "ineligible", "insufficient_data"})
VALIDATION_STATUSES = frozenset({"valid", "invalid", "warnings", "not_generated"})
FINDING_LEVELS = frozenset({"ERROR", "WARNING", "INFO"})
SEVERITIES = frozenset({"critical", "high", "medium", "low", "info"})

FINDING_CATEGORIES = frozenset({
    "eligibility",
    "syntax",
    "structure",
    "required_property",
    "recommended_property",
    "property_type",
    "url",
    "content_consistency",
    "evidence",
    "unsupported_property",
    "missing_data",
})

FABRICATED_PROPERTY_DENYLIST = frozenset({
    "price",
    "priceCurrency",
    "sku",
    "availability",
    "aggregateRating",
    "review",
    "reviewCount",
    "ratingValue",
    "telephone",
    "openingHours",
    "geo",
    "address",
    "datePublished",
    "dateModified",
    "author",
    "publisher",
    "image",
    "logo",
})


@dataclass(frozen=True)
class SeoSchemaLimits:
    max_json_ld_bytes: int = 32_000
    max_properties: int = 50
    max_nesting_depth: int = 6
    max_findings: int = 100
    max_schema_types_per_run: int = 8
    max_prompt_chars: int = 60_000
    schema_rate_per_hour: int = 24


DEFAULT_SCHEMA_LIMITS = SeoSchemaLimits()
