from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


class SeoEvidenceRef(BaseModel):
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


class SeoRecommendationItem(BaseModel):
    type: Literal[
        "technical_seo",
        "content_refresh",
        "keyword_targeting",
        "topic_expansion",
        "content_gap",
        "metadata_optimization",
        "page_structure",
        "search_intent",
        "competitor_gap",
    ]
    title: str = Field(min_length=1, max_length=255)
    summary: str = Field(min_length=1, max_length=2000)
    rationale: str = Field(min_length=1, max_length=4000)
    priority: Literal["high", "medium", "low"]
    impact: Literal["high", "medium", "low"]
    effort: Literal["high", "medium", "low"]
    confidence: float = Field(ge=0.0, le=1.0)
    evidence_refs: list[SeoEvidenceRef] = Field(min_length=1, max_length=10)
    affected_urls: list[str] = Field(default_factory=list, max_length=20)
    affected_keywords: list[str] = Field(default_factory=list, max_length=20)
    affected_topics: list[str] = Field(default_factory=list, max_length=10)
    competitor_context: dict = Field(default_factory=dict)
    recommended_action: str = Field(min_length=1, max_length=2000)
    expected_outcome: str = Field(min_length=1, max_length=2000)
    limitations: list[str] = Field(default_factory=list, max_length=10)


class SeoRecommendationsGenerated(BaseModel):
    recommendations: list[SeoRecommendationItem] = Field(default_factory=list, max_length=15)
    data_limitations: list[str] = Field(default_factory=list, max_length=20)

    @field_validator("recommendations")
    @classmethod
    def non_empty_if_present(cls, v: list[SeoRecommendationItem]) -> list[SeoRecommendationItem]:
        return v


class SeoRecommendationGenerateRequest(BaseModel):
    sync_id: UUID | None = None


class SeoRecommendationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    run_id: UUID
    sync_id: UUID | None
    recommendation_type: str
    title: str
    summary: str
    rationale: str
    priority: str
    impact: str
    effort: str
    confidence: float
    status: str
    evidence_refs: list
    affected_urls: list
    affected_keywords: list
    affected_topics: list
    competitor_context: dict
    recommended_action: str
    expected_outcome: str
    limitations: list
    provider: str
    model: str
    prompt_version: str
    algorithm_version: str
    created_at: datetime | None = None


class SeoRecommendationSummaryOut(BaseModel):
    sync_id: UUID | None = None
    total_recommendations: int
    by_type: dict[str, int]
    by_priority: dict[str, int]
    algorithm_version: str
    prompt_version: str
    disclaimer: str
