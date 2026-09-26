"""SEO crawl persistence — read-only site crawl runs and page observations."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSON
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.enums import SeoCrawlStatus


class SeoCrawl(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "seo_crawls"

    organization_id: Mapped[UUID] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    client_id: Mapped[UUID | None] = mapped_column(ForeignKey("clients.id", ondelete="CASCADE"), nullable=True, index=True)
    root_url: Mapped[str] = mapped_column(String(2048), nullable=False)
    status: Mapped[SeoCrawlStatus] = mapped_column(
        String(16),
        default=SeoCrawlStatus.queued,
        nullable=False,
        index=True,
    )
    config: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    stats: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    cancel_requested: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    job_id: Mapped[UUID | None] = mapped_column(ForeignKey("background_jobs.id", ondelete="SET NULL"), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class SeoCrawlPage(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "seo_crawl_pages"

    crawl_id: Mapped[UUID] = mapped_column(ForeignKey("seo_crawls.id", ondelete="CASCADE"), index=True)
    organization_id: Mapped[UUID] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    url: Mapped[str] = mapped_column(String(2048), nullable=False)
    final_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    depth: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    referrer_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    http_status: Mapped[int | None] = mapped_column(Integer, nullable=True)
    content_type: Mapped[str | None] = mapped_column(String(255), nullable=True)
    response_bytes: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    redirect_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    response_time_ms: Mapped[float | None] = mapped_column(nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    observations: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    data_source: Mapped[str] = mapped_column(String(32), default="http_crawl", nullable=False)
