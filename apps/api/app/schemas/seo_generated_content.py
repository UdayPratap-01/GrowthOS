from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class InternalLinkItem(BaseModel):
    url: str = Field(min_length=1, max_length=2048)
    anchor_text: str = Field(min_length=1, max_length=255)


class ContentSubsection(BaseModel):
    heading: str = Field(min_length=1, max_length=255)
    level: Literal["H3"]
    content: str = Field(min_length=1, max_length=8000)


class ContentSection(BaseModel):
    heading: str = Field(min_length=1, max_length=255)
    level: Literal["H2"]
    content: str = Field(min_length=1, max_length=12000)
    subsections: list[ContentSubsection] = Field(default_factory=list, max_length=5)


class SeoGeneratedContentItem(BaseModel):
    title: str = Field(min_length=1, max_length=255)
    content_type: Literal[
        "informational_article",
        "blog_article",
        "guide",
        "comparison",
        "service_content",
        "landing_page_draft",
        "content_refresh",
    ]
    introduction: str = Field(min_length=1, max_length=4000)
    sections: list[ContentSection] = Field(min_length=1, max_length=15)
    conclusion: str = Field(min_length=1, max_length=4000)
    meta_title: str = Field(min_length=1, max_length=70)
    meta_description: str = Field(min_length=1, max_length=160)
    internal_links: list[InternalLinkItem] = Field(default_factory=list, max_length=20)
    limitations: list[str] = Field(default_factory=list, max_length=10)


class SeoGeneratedContentOutput(BaseModel):
    content: SeoGeneratedContentItem
    data_limitations: list[str] = Field(default_factory=list, max_length=20)


class SeoContentGenerateRequest(BaseModel):
    content_brief_id: UUID


class SeoGeneratedContentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    content_brief_id: UUID
    recommendation_id: UUID
    title: str
    slug: str
    content_type: str
    status: str
    content: str
    structured_sections: list
    primary_keyword: str
    secondary_keywords: list
    target_topic: str | None
    target_url: str | None
    meta_title: str
    meta_description: str
    outline_used: list
    internal_link_targets: list
    evidence_refs: list
    limitations: list
    provider: str
    model: str
    prompt_version: str
    algorithm_version: str
    word_count: int
    created_at: datetime | None = None


class SeoGeneratedContentSourceOut(BaseModel):
    content_id: UUID
    content_brief_id: UUID
    recommendation_id: UUID
    brief_snapshot: dict
    evidence_refs: list
    limitations: list
    disclaimer: str
