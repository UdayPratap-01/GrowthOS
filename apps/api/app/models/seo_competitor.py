"""SEO competitor and content-gap persistence (M9.6)."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSON
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.enums import SeoCrawlStatus


class SeoCompetitor(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "seo_competitors"
    __table_args__ = (
        UniqueConstraint("organization_id", "root_url", name="uq_seo_competitors_org_root_url"),
    )

    organization_id: Mapped[UUID] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    display_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    root_url: Mapped[str] = mapped_column(String(2048), nullable=False, index=True)
    domain: Mapped[str] = mapped_column(String(512), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(16), default="active", nullable=False, index=True)


class SeoCompetitorCrawl(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "seo_competitor_crawls"

    competitor_id: Mapped[UUID] = mapped_column(ForeignKey("seo_competitors.id", ondelete="CASCADE"), index=True)
    organization_id: Mapped[UUID] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    root_url: Mapped[str] = mapped_column(String(2048), nullable=False)
    status: Mapped[SeoCrawlStatus] = mapped_column(String(16), default=SeoCrawlStatus.queued, nullable=False, index=True)
    config: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    stats: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    cancel_requested: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    job_id: Mapped[UUID | None] = mapped_column(ForeignKey("background_jobs.id", ondelete="SET NULL"), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class SeoCompetitorPage(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "seo_competitor_pages"
    __table_args__ = (
        UniqueConstraint("crawl_id", "url", name="uq_seo_competitor_pages_crawl_url"),
    )

    crawl_id: Mapped[UUID] = mapped_column(ForeignKey("seo_competitor_crawls.id", ondelete="CASCADE"), index=True)
    competitor_id: Mapped[UUID] = mapped_column(ForeignKey("seo_competitors.id", ondelete="CASCADE"), index=True)
    organization_id: Mapped[UUID] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    url: Mapped[str] = mapped_column(String(2048), nullable=False, index=True)
    final_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    depth: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    http_status: Mapped[int | None] = mapped_column(Integer, nullable=True)
    content_type: Mapped[str | None] = mapped_column(String(255), nullable=True)
    title: Mapped[str | None] = mapped_column(Text, nullable=True)
    meta_description: Mapped[str | None] = mapped_column(Text, nullable=True)
    h1: Mapped[str | None] = mapped_column(Text, nullable=True)
    headings: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    topic_label: Mapped[str | None] = mapped_column(String(512), nullable=True)
    token_terms: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    word_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    structured_data_present: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    observations: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    fetched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)


class ContentGapAnalysisRun(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "content_gap_analysis_runs"

    organization_id: Mapped[UUID] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    sync_id: Mapped[UUID | None] = mapped_column(ForeignKey("search_console_syncs.id", ondelete="SET NULL"), nullable=True, index=True)
    status: Mapped[str] = mapped_column(String(16), default="completed", nullable=False, index=True)
    algorithm_version: Mapped[str] = mapped_column(String(32), nullable=False, default="competitor_gap_v1", index=True)
    stats: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)


class ContentGap(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "content_gaps"
    __table_args__ = (
        UniqueConstraint("organization_id", "dedupe_key", name="uq_content_gaps_org_dedupe"),
    )

    organization_id: Mapped[UUID] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    analysis_run_id: Mapped[UUID] = mapped_column(ForeignKey("content_gap_analysis_runs.id", ondelete="CASCADE"), index=True)
    sync_id: Mapped[UUID | None] = mapped_column(ForeignKey("search_console_syncs.id", ondelete="SET NULL"), nullable=True, index=True)
    competitor_id: Mapped[UUID | None] = mapped_column(ForeignKey("seo_competitors.id", ondelete="SET NULL"), nullable=True, index=True)
    competitor_crawl_id: Mapped[UUID | None] = mapped_column(ForeignKey("seo_competitor_crawls.id", ondelete="SET NULL"), nullable=True)
    competitor_page_id: Mapped[UUID | None] = mapped_column(ForeignKey("seo_competitor_pages.id", ondelete="SET NULL"), nullable=True)
    competitor_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    gap_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    topic_label: Mapped[str] = mapped_column(String(512), nullable=False)
    user_topic_id: Mapped[UUID | None] = mapped_column(ForeignKey("topic_clusters.id", ondelete="SET NULL"), nullable=True, index=True)
    user_query: Mapped[str | None] = mapped_column(Text, nullable=True)
    competitor_title: Mapped[str | None] = mapped_column(Text, nullable=True)
    competitor_h1: Mapped[str | None] = mapped_column(Text, nullable=True)
    similarity: Mapped[float] = mapped_column(Float, default=0, nullable=False)
    match_strength: Mapped[str] = mapped_column(String(32), nullable=False, default="weak_match", index=True)
    evidence: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    explanation: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="open", nullable=False, index=True)
    algorithm_version: Mapped[str] = mapped_column(String(32), nullable=False, default="competitor_gap_v1", index=True)
    dedupe_key: Mapped[str] = mapped_column(String(512), nullable=False)
