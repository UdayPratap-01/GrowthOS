"""Post-generation grounding validation for SEO content briefs (M9.8)."""

from __future__ import annotations

from app.schemas.seo_content_brief import SeoContentBriefGenerated, SeoContentBriefItem
from app.seo.content_briefs.context import BriefContext
from app.seo.content_briefs.thresholds import (
    BRIEF_TYPES,
    EVIDENCE_SOURCES,
    OUTLINE_LEVELS,
    SEARCH_INTENT_TYPES,
    UNAVAILABLE_PRIMARY_KEYWORD,
)


class BriefGroundingError(Exception):
    def __init__(self, message: str) -> None:
        super().__init__(message)


def validate_brief_output(output: SeoContentBriefGenerated, ctx: BriefContext) -> SeoContentBriefItem:
    brief = output.brief
    _validate_brief(brief, ctx)
    return brief


def _validate_brief(brief: SeoContentBriefItem, ctx: BriefContext) -> None:
    if brief.brief_type not in BRIEF_TYPES:
        raise BriefGroundingError(f"invalid brief_type: {brief.brief_type}")

    if brief.primary_keyword != UNAVAILABLE_PRIMARY_KEYWORD and brief.primary_keyword not in ctx.keywords:
        raise BriefGroundingError(f"primary_keyword not in evidence: {brief.primary_keyword}")

    for kw in brief.secondary_keywords:
        if kw not in ctx.keywords:
            raise BriefGroundingError(f"secondary keyword not in evidence: {kw}")

    if brief.target_url and brief.target_url not in ctx.urls:
        raise BriefGroundingError(f"target_url not in evidence: {brief.target_url}")

    if brief.target_topic:
        if brief.target_topic not in ctx.topic_labels.values() and brief.target_topic not in ctx.topic_ids:
            if not any(brief.target_topic in (r.get("topic_label") or "") for r in ctx.resolved_evidence):
                raise BriefGroundingError(f"target_topic not in evidence: {brief.target_topic}")

    if brief.search_intent.type not in SEARCH_INTENT_TYPES:
        raise BriefGroundingError(f"invalid search_intent type: {brief.search_intent.type}")

    for ref in brief.evidence_refs:
        if ref.source not in EVIDENCE_SOURCES:
            raise BriefGroundingError(f"invalid evidence source: {ref.source}")
        allowed = ctx.index.get(ref.source, set())
        if ref.id not in allowed:
            raise BriefGroundingError(f"evidence id not in context: {ref.source}/{ref.id}")

    for link in brief.internal_link_targets:
        if link not in ctx.internal_urls:
            raise BriefGroundingError(f"internal link not in crawl evidence: {link}")

    for section in brief.outline:
        if section.level not in OUTLINE_LEVELS:
            raise BriefGroundingError(f"invalid outline level: {section.level}")
