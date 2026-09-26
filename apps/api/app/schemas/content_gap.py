from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class ContentGapAnalyzeRequest(BaseModel):
    sync_id: UUID | None = None


class ContentGapOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    sync_id: UUID | None
    competitor_id: UUID | None
    competitor_url: str | None
    gap_type: str
    topic_label: str
    user_topic_id: UUID | None
    user_query: str | None
    competitor_title: str | None
    competitor_h1: str | None
    similarity: float
    match_strength: str
    evidence: dict
    explanation: str
    status: str
    algorithm_version: str
    created_at: datetime | None = None


class ContentGapSummaryOut(BaseModel):
    sync_id: UUID | None = None
    algorithm_version: str
    total_gaps: int
    by_gap_type: dict[str, int]
    by_match_strength: dict[str, int]
    active_competitors: int
    competitor_pages_observed: int
    has_competitor_data: bool
    disclaimer: str
