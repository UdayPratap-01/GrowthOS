"""Pydantic schemas for SEO dashboard aggregation (M9.14)."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


class DashboardSectionMeta(BaseModel):
    available: bool
    empty_message: str | None = None


class DashboardOverviewOut(BaseModel):
    latest_crawl_id: UUID | None = None
    latest_crawl_status: str | None = None
    pages_crawled: int = 0
    technical_findings: int = 0
    critical_high_findings: int = 0
    keyword_opportunities: int = 0
    topic_clusters: int = 0
    content_gaps: int = 0
    recommendations: int = 0
    content_briefs: int = 0
    generated_content: int = 0
    internal_link_opportunities: int = 0
    schema_artifacts: int = 0
    pending_actions: int = 0


class DashboardTechnicalOut(DashboardSectionMeta):
    crawl_id: UUID | None = None
    root_url: str | None = None
    total_findings: int = 0
    by_severity: dict[str, int] = Field(default_factory=dict)
    by_category: dict[str, int] = Field(default_factory=dict)
    affected_pages: int = 0
    analysis_status: str | None = None


class DashboardSearchConsoleOut(DashboardSectionMeta):
    site_url: str | None = None
    connected: bool = False
    clicks: int | None = None
    impressions: int | None = None
    ctr: float | None = None
    opportunity_count: int = 0
    by_priority: dict[str, int] = Field(default_factory=dict)
    last_sync_status: str | None = None


class DashboardKeywordsOut(DashboardSectionMeta):
    sync_id: UUID | None = None
    total_opportunities: int = 0
    unique_queries: int = 0
    by_priority: dict[str, int] = Field(default_factory=dict)
    high_priority_count: int = 0


class DashboardTopicsOut(DashboardSectionMeta):
    sync_id: UUID | None = None
    total_topics: int = 0
    total_clustered_queries: int = 0
    singleton_topics: int = 0


class DashboardCompetitorsOut(DashboardSectionMeta):
    active_competitors: int = 0
    completed_crawls: int = 0
    total_gaps: int = 0
    by_gap_type: dict[str, int] = Field(default_factory=dict)
    has_competitor_data: bool = False


class DashboardContentOut(DashboardSectionMeta):
    briefs_count: int = 0
    briefs_draft: int = 0
    generated_count: int = 0
    generated_draft: int = 0
    content_with_optimization: int = 0
    content_with_schema: int = 0
    content_with_internal_links: int = 0


class DashboardOnPageOut(DashboardSectionMeta):
    optimization_runs: int = 0
    total_findings: int = 0
    by_severity: dict[str, int] = Field(default_factory=dict)


class DashboardSchemaOut(DashboardSectionMeta):
    artifact_count: int = 0
    by_validation_status: dict[str, int] = Field(default_factory=dict)
    by_eligibility_status: dict[str, int] = Field(default_factory=dict)
    finding_count: int = 0


class DashboardInternalLinksOut(DashboardSectionMeta):
    total_opportunities: int = 0
    suggested_count: int = 0
    high_confidence_count: int = 0
    by_type: dict[str, int] = Field(default_factory=dict)
    orphan_page_count: int = 0


class DashboardActionsOut(DashboardSectionMeta):
    pending: int = 0
    approved: int = 0
    completed: int = 0
    rejected: int = 0
    failed: int = 0
    review_only: int = 0
    recent_pending: list[dict] = Field(default_factory=list)


class DashboardAttentionItemOut(BaseModel):
    source: str
    item_type: str
    title: str
    reason: str
    severity_or_priority: str | None = None
    reference_id: str | None = None
    link_path: str | None = None


class DashboardAttentionOut(DashboardSectionMeta):
    items: list[DashboardAttentionItemOut] = Field(default_factory=list)


class DashboardMonitoringOut(DashboardSectionMeta):
    monitoring_enabled: bool = False
    scheduler_enabled: bool = False
    open_alerts: int = 0
    last_monitor_run_at: datetime | None = None
    last_crawl_at: datetime | None = None
    last_sync_at: datetime | None = None
    last_failure_reason: str | None = None
    next_scheduled_run: datetime | None = None


class SeoDashboardOut(BaseModel):
    generated_at: datetime
    disclaimer: str
    overview: DashboardOverviewOut
    technical: DashboardTechnicalOut
    search_console: DashboardSearchConsoleOut
    keywords: DashboardKeywordsOut
    topics: DashboardTopicsOut
    competitors: DashboardCompetitorsOut
    content: DashboardContentOut
    on_page: DashboardOnPageOut
    schema_panel: DashboardSchemaOut
    internal_links: DashboardInternalLinksOut
    actions: DashboardActionsOut
    attention: DashboardAttentionOut
    monitoring: DashboardMonitoringOut
