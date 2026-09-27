from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class OnPageSuggestionItem(BaseModel):
    finding_type: str = Field(min_length=1, max_length=64)
    suggested_change: str = Field(min_length=1, max_length=4000)
    explanation: str = Field(min_length=1, max_length=2000)


class SeoOnPageOptimizerAiOutput(BaseModel):
    suggestions: list[OnPageSuggestionItem] = Field(default_factory=list, max_length=10)
    limitations: list[str] = Field(default_factory=list, max_length=10)


class SeoOnPageFindingOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    run_id: UUID
    generated_content_id: UUID
    content_brief_id: UUID
    finding_type: str
    category: str
    severity: str
    priority: str
    status: str
    title: str
    summary: str
    rationale: str
    current_value: str | None
    expected_value: str | None
    recommendation: str
    evidence_refs: list[dict]
    affected_section: str | None
    affected_element: str | None
    suggested_change: str | None
    created_at: datetime


class SeoOnPageOptimizationRunOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    generated_content_id: UUID
    content_brief_id: UUID
    status: str
    analysis_key: str
    stats: dict
    limitations: list[str]
    provider: str
    model: str
    prompt_version: str
    algorithm_version: str
    ai_enriched: bool
    created_at: datetime


class SeoOnPageOptimizationOut(BaseModel):
    run: SeoOnPageOptimizationRunOut
    findings: list[SeoOnPageFindingOut]
    disclaimer: str
