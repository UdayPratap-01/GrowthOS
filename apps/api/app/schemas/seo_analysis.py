from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class SeoFindingOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    crawl_id: UUID
    page_id: UUID | None = None
    rule_id: str
    category: str
    severity: str
    status: str
    title: str
    description: str
    evidence: dict = Field(default_factory=dict)
    observed_value: str | None = None
    expected_or_heuristic: str | None = None
    recommendation: str | None = None
    url: str | None = None
    created_at: datetime | None = None


class SeoFindingSummaryOut(BaseModel):
    crawl_id: UUID
    total_findings: int
    by_severity: dict[str, int]
    by_category: dict[str, int]
    affected_pages: int
    analysis_status: str
    disclaimer: str


class SeoFindingCompareOut(BaseModel):
    base_crawl_id: UUID
    compare_crawl_id: UUID
    new_findings: int
    resolved_findings: int
    persistent_findings: int
    new: list[dict] = Field(default_factory=list)
    resolved: list[dict] = Field(default_factory=list)
    disclaimer: str
