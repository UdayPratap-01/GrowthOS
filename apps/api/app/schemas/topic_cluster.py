from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class TopicAnalyzeRequest(BaseModel):
    sync_id: UUID | None = None


class TopicClusterOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    sync_id: UUID
    site_url: str
    cluster_key: str
    topic_label: str
    representative_query: str
    query_count: int
    page_count: int
    total_clicks: float
    total_impressions: float
    aggregate_ctr: float
    weighted_average_position: float
    opportunity_count: int
    multi_page_signal: bool
    is_singleton: bool
    algorithm_version: str
    status: str
    created_at: datetime | None = None


class TopicClusterQueryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    query: str
    normalized_query: str
    similarity_score: float
    membership_reason: str
    clicks: float
    impressions: float
    ctr: float
    average_position: float
    opportunity_count: int
    is_representative: bool


class TopicClusterPageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    page_url: str
    clicks: float
    impressions: float
    ctr: float
    average_position: float
    is_primary: bool


class TopicSummaryOut(BaseModel):
    sync_id: UUID | None = None
    site_url: str | None = None
    algorithm_version: str
    total_topics: int
    total_clustered_queries: int
    singleton_topics: int
    average_queries_per_topic: float
    multi_page_topic_count: int
    top_by_impressions: list[TopicClusterOut] = Field(default_factory=list)
    top_by_clicks: list[TopicClusterOut] = Field(default_factory=list)
    top_by_opportunity_count: list[TopicClusterOut] = Field(default_factory=list)
    disclaimer: str
