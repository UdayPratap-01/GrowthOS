"""Build bounded generation context from M9.8 content brief (M9.9)."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from app.models.seo_content_brief import SeoContentBrief
from app.seo.content_generation.thresholds import UNAVAILABLE_KEYWORD


@dataclass
class GenerationContext:
    content_brief_id: UUID
    recommendation_id: UUID
    brief_title: str
    brief_type: str
    suggested_content_type: str
    primary_keyword: str
    secondary_keywords: list[str] = field(default_factory=list)
    target_topic: str | None = None
    search_intent: dict[str, Any] = field(default_factory=dict)
    target_url: str | None = None
    content_goal: str = ""
    target_audience: str = ""
    suggested_angle: str = ""
    outline: list[dict[str, Any]] = field(default_factory=list)
    questions_to_answer: list[str] = field(default_factory=list)
    entities_to_cover: list[str] = field(default_factory=list)
    internal_link_targets: list[str] = field(default_factory=list)
    content_requirements: list[str] = field(default_factory=list)
    seo_requirements: list[str] = field(default_factory=list)
    evidence_refs: list[dict[str, Any]] = field(default_factory=list)
    limitations: list[str] = field(default_factory=list)
    competitor_context: dict[str, Any] = field(default_factory=dict)

    def context_hash(self) -> str:
        payload = {
            "brief_id": str(self.content_brief_id),
            "outline": self.outline,
            "keywords": [self.primary_keyword, *self.secondary_keywords],
            "internal_links": self.internal_link_targets,
        }
        return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()[:64]

    def as_prompt_dict(self) -> dict[str, Any]:
        return {
            "content_brief_id": str(self.content_brief_id),
            "recommendation_id": str(self.recommendation_id),
            "title": self.brief_title,
            "brief_type": self.brief_type,
            "suggested_content_type": self.suggested_content_type,
            "primary_keyword": self.primary_keyword,
            "secondary_keywords": self.secondary_keywords,
            "target_topic": self.target_topic,
            "search_intent": self.search_intent,
            "target_url": self.target_url,
            "content_goal": self.content_goal,
            "target_audience": self.target_audience,
            "suggested_angle": self.suggested_angle,
            "outline": self.outline,
            "questions_to_answer": self.questions_to_answer,
            "entities_to_cover": self.entities_to_cover,
            "internal_link_targets": self.internal_link_targets,
            "content_requirements": self.content_requirements,
            "seo_requirements": self.seo_requirements,
            "evidence_refs": self.evidence_refs,
            "limitations": self.limitations,
            "competitor_context": self.competitor_context,
        }

    def allowed_keywords(self) -> set[str]:
        kws = set()
        if self.primary_keyword and self.primary_keyword != UNAVAILABLE_KEYWORD:
            kws.add(self.primary_keyword.lower())
        kws.update(k.lower() for k in self.secondary_keywords)
        return kws

    def outline_headings(self) -> list[str]:
        return [str(s.get("heading", "")).strip().lower() for s in self.outline if s.get("heading")]


def build_generation_context(brief: SeoContentBrief) -> GenerationContext:
    return GenerationContext(
        content_brief_id=brief.id,
        recommendation_id=brief.recommendation_id,
        brief_title=brief.title,
        brief_type=brief.brief_type,
        suggested_content_type=brief.suggested_content_type,
        primary_keyword=brief.primary_keyword,
        secondary_keywords=list(brief.secondary_keywords or []),
        target_topic=brief.target_topic,
        search_intent=dict(brief.search_intent or {}),
        target_url=brief.target_url,
        content_goal=brief.content_goal,
        target_audience=brief.target_audience,
        suggested_angle=brief.suggested_angle,
        outline=list(brief.outline or []),
        questions_to_answer=list(brief.questions_to_answer or []),
        entities_to_cover=list(brief.entities_to_cover or []),
        internal_link_targets=list(brief.internal_link_targets or []),
        content_requirements=list(brief.content_requirements or []),
        seo_requirements=list(brief.seo_requirements or []),
        evidence_refs=list(brief.evidence_refs or []),
        limitations=list(brief.limitations or []),
        competitor_context=dict(brief.competitor_context or {}),
    )


def slugify(title: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
    return slug[:128] or "seo-content"


def count_words(text: str) -> int:
    return len(re.findall(r"\b\w+\b", text or ""))
