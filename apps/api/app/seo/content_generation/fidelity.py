"""Brief fidelity validation for generated SEO content (M9.9)."""

from __future__ import annotations

import re

from app.schemas.seo_generated_content import SeoGeneratedContentItem, SeoGeneratedContentOutput
from app.seo.content_generation.context import GenerationContext
from app.seo.content_generation.thresholds import (
    CONTENT_TYPES,
    DEFAULT_SEO_CONTENT_GENERATION_LIMITS,
    FABRICATION_PATTERNS,
    UNAVAILABLE_KEYWORD,
)


class FidelityError(Exception):
    def __init__(self, message: str) -> None:
        super().__init__(message)


def validate_generated_content(
    output: SeoGeneratedContentOutput,
    ctx: GenerationContext,
) -> SeoGeneratedContentItem:
    content = output.content
    _validate_content_type(content.content_type)
    _validate_internal_links(content, ctx)
    _validate_primary_keyword(content, ctx)
    _validate_outline_adherence(content, ctx)
    _validate_questions(content, ctx)
    _validate_entities(content, ctx)
    _validate_factuality(content)
    _validate_metadata_lengths(content)
    return content


def _combined_text(content: SeoGeneratedContentItem) -> str:
    parts = [content.title, content.introduction, content.conclusion, content.meta_title, content.meta_description]
    for section in content.sections:
        parts.append(section.heading)
        parts.append(section.content)
        for sub in section.subsections:
            parts.append(sub.heading)
            parts.append(sub.content)
    return " ".join(p for p in parts if p).lower()


def _validate_content_type(content_type: str) -> None:
    if content_type not in CONTENT_TYPES:
        raise FidelityError(f"invalid content_type: {content_type}")


def _validate_internal_links(content: SeoGeneratedContentItem, ctx: GenerationContext) -> None:
    allowed = set(ctx.internal_link_targets)
    for link in content.internal_links:
        if link.url not in allowed:
            raise FidelityError(f"internal link not in brief: {link.url}")


def _validate_primary_keyword(content: SeoGeneratedContentItem, ctx: GenerationContext) -> None:
    pk = ctx.primary_keyword
    if not pk or pk == UNAVAILABLE_KEYWORD:
        return
    text = _combined_text(content)
    if pk.lower() not in text:
        raise FidelityError(f"primary keyword not represented in content: {pk}")


def _validate_outline_adherence(content: SeoGeneratedContentItem, ctx: GenerationContext) -> None:
    expected = [h for h in ctx.outline_headings() if h]
    if not expected:
        return
    section_headings = {s.heading.lower() for s in content.sections}
    for sub in content.sections:
        section_headings.update(s.heading.lower() for s in sub.subsections)
    matched = sum(1 for h in expected if any(h in sh or sh in h for sh in section_headings))
    if matched < max(1, len(expected) // 2):
        raise FidelityError("generated sections do not follow brief outline sufficiently")


def _validate_questions(content: SeoGeneratedContentItem, ctx: GenerationContext) -> None:
    if not ctx.questions_to_answer:
        return
    text = _combined_text(content)
    answered = 0
    for q in ctx.questions_to_answer:
        tokens = [t for t in re.findall(r"[a-z0-9]+", q.lower()) if len(t) > 3]
        if tokens and any(t in text for t in tokens[:3]):
            answered += 1
    if answered == 0 and ctx.questions_to_answer:
        raise FidelityError("required questions not addressed in generated content")


def _validate_entities(content: SeoGeneratedContentItem, ctx: GenerationContext) -> None:
    if not ctx.entities_to_cover:
        return
    text = _combined_text(content)
    represented = sum(1 for e in ctx.entities_to_cover if e.lower() in text)
    if represented == 0:
        raise FidelityError("required entities not represented in generated content")


def _validate_factuality(content: SeoGeneratedContentItem) -> None:
    text = _combined_text(content)
    for pattern in FABRICATION_PATTERNS:
        if pattern in text:
            raise FidelityError(f"fabricated claim pattern detected: {pattern}")


def _validate_metadata_lengths(content: SeoGeneratedContentItem) -> None:
    limits = DEFAULT_SEO_CONTENT_GENERATION_LIMITS
    if len(content.meta_title) > limits.meta_title_max:
        raise FidelityError("meta_title too long")
    if len(content.meta_description) > limits.meta_description_max:
        raise FidelityError("meta_description too long")
