"""Pydantic schemas for SEO internal-link engine (M9.12)."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


class SeoInternalLinkGenerateRequest(BaseModel):
    use_ai: bool = True


class SeoInternalLinkOpportunityOut(BaseModel):
    id: UUID
    run_id: UUID
    generated_content_id: UUID | None
    content_brief_id: UUID | None
    source_url: str
    target_url: str
    source_crawl_page_id: UUID | None
    target_crawl_page_id: UUID | None
    anchor_text: str
    anchor_alternatives: list[str]
    opportunity_type: str
    relationship_reason: str
    source_topic: str | None
    target_topic: str | None
    source_keywords: list[str]
    target_keywords: list[str]
    relevance_score: float
    confidence: str
    score_breakdown: dict
    evidence_refs: list[dict]
    limitations: list[str]
    status: str
    created_at: datetime | None = None

    model_config = {"from_attributes": True}


class SeoInternalLinkRunOut(BaseModel):
    id: UUID
    organization_id: UUID
    generated_content_id: UUID | None
    content_brief_id: UUID | None
    crawl_id: UUID | None
    status: str
    analysis_key: str
    stats: dict
    limitations: list[str]
    provider: str
    model: str
    prompt_version: str
    algorithm_version: str
    ai_enriched: bool
    created_at: datetime | None = None

    model_config = {"from_attributes": True}


class SeoInternalLinkReportOut(BaseModel):
    run: SeoInternalLinkRunOut
    opportunities: list[SeoInternalLinkOpportunityOut]
    disclaimer: str


class SeoInternalLinkSummaryOut(BaseModel):
    total: int
    by_type: dict[str, int]
    by_confidence: dict[str, int]
    disclaimer: str


class SeoInternalLinkAiSuggestion(BaseModel):
    source_url: str
    target_url: str
    rationale: str = ""
    anchor_alternatives: list[str] = Field(default_factory=list)


class SeoInternalLinkAiOutput(BaseModel):
    suggestions: list[SeoInternalLinkAiSuggestion] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
