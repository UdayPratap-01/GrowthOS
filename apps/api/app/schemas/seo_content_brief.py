from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class SeoBriefEvidenceRef(BaseModel):
    source: Literal[
        "seo_finding",
        "keyword_opportunity",
        "topic_cluster",
        "content_gap",
        "search_console_opportunity",
        "competitor_page",
    ]
    id: str = Field(min_length=1, max_length=64)
    reason: str = Field(min_length=1, max_length=500)


class SearchIntentInterpretation(BaseModel):
    type: Literal["informational", "commercial", "transactional", "navigational", "mixed", "unknown"]
    confidence: float = Field(ge=0.0, le=1.0)
    basis: list[str] = Field(default_factory=list, max_length=10)
    interpretation_note: str = Field(
        default="AI interpretation — not verified by Google.",
        max_length=500,
    )


class BriefOutlineSection(BaseModel):
    heading: str = Field(min_length=1, max_length=255)
    level: Literal["H2", "H3"]
    purpose: str = Field(min_length=1, max_length=1000)
    key_points: list[str] = Field(default_factory=list, max_length=10)


class SeoContentBriefItem(BaseModel):
    title: str = Field(min_length=1, max_length=255)
    brief_type: Literal[
        "content_refresh",
        "keyword_targeting",
        "topic_expansion",
        "content_gap",
        "search_intent",
        "competitor_gap",
    ]
    primary_keyword: str = Field(min_length=1, max_length=255)
    secondary_keywords: list[str] = Field(default_factory=list, max_length=15)
    target_topic: str | None = Field(default=None, max_length=512)
    search_intent: SearchIntentInterpretation
    target_url: str | None = Field(default=None, max_length=2048)
    content_goal: str = Field(min_length=1, max_length=2000)
    target_audience: str = Field(min_length=1, max_length=1000)
    suggested_content_type: str = Field(min_length=1, max_length=128)
    suggested_angle: str = Field(min_length=1, max_length=2000)
    outline: list[BriefOutlineSection] = Field(min_length=1, max_length=12)
    questions_to_answer: list[str] = Field(default_factory=list, max_length=12)
    entities_to_cover: list[str] = Field(default_factory=list, max_length=20)
    internal_link_targets: list[str] = Field(default_factory=list, max_length=20)
    content_requirements: list[str] = Field(default_factory=list, max_length=15)
    seo_requirements: list[str] = Field(default_factory=list, max_length=15)
    limitations: list[str] = Field(default_factory=list, max_length=10)
    evidence_refs: list[SeoBriefEvidenceRef] = Field(min_length=1, max_length=10)


class SeoContentBriefGenerated(BaseModel):
    brief: SeoContentBriefItem
    data_limitations: list[str] = Field(default_factory=list, max_length=20)


class SeoContentBriefGenerateRequest(BaseModel):
    recommendation_id: UUID


class SeoContentBriefOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    recommendation_id: UUID
    title: str
    brief_type: str
    primary_keyword: str
    secondary_keywords: list
    target_topic: str | None
    search_intent: dict
    target_url: str | None
    content_goal: str
    target_audience: str
    suggested_content_type: str
    suggested_angle: str
    outline: list
    questions_to_answer: list
    entities_to_cover: list
    internal_link_targets: list
    competitor_context: dict
    evidence_refs: list
    source_recommendation_ids: list
    content_requirements: list
    seo_requirements: list
    limitations: list
    status: str
    provider: str
    model: str
    prompt_version: str
    algorithm_version: str
    created_at: datetime | None = None
