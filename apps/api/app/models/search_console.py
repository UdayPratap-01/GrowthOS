"""Search Console intelligence persistence — tenant-scoped read-only GSC data."""

from __future__ import annotations

from datetime import date, datetime
from uuid import UUID

from sqlalchemy import Date, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSON
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.enums import SearchConsoleOpportunityPriority, SearchConsoleSyncStatus


class SearchConsoleSync(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "search_console_syncs"

    organization_id: Mapped[UUID] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    client_id: Mapped[UUID | None] = mapped_column(ForeignKey("clients.id", ondelete="CASCADE"), nullable=True, index=True)
    site_url: Mapped[str] = mapped_column(String(2048), nullable=False)
    sync_key: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[SearchConsoleSyncStatus] = mapped_column(String(16), nullable=False, index=True)
    preset: Mapped[str | None] = mapped_column(String(32), nullable=True)
    requested_start_date: Mapped[date] = mapped_column(Date, nullable=False)
    requested_end_date: Mapped[date] = mapped_column(Date, nullable=False)
    effective_start_date: Mapped[date] = mapped_column(Date, nullable=False)
    effective_end_date: Mapped[date] = mapped_column(Date, nullable=False)
    compare_start_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    compare_end_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    row_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    opportunity_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    meta: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)


class SearchConsolePerformanceRow(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "search_console_performance_rows"

    sync_id: Mapped[UUID] = mapped_column(ForeignKey("search_console_syncs.id", ondelete="CASCADE"), index=True)
    organization_id: Mapped[UUID] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    site_url: Mapped[str] = mapped_column(String(2048), nullable=False)
    dimension_type: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    query: Mapped[str | None] = mapped_column(Text, nullable=True)
    page_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    country: Mapped[str | None] = mapped_column(String(8), nullable=True)
    device: Mapped[str | None] = mapped_column(String(16), nullable=True)
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    end_date: Mapped[date] = mapped_column(Date, nullable=False)
    clicks: Mapped[float] = mapped_column(Float, default=0, nullable=False)
    impressions: Mapped[float] = mapped_column(Float, default=0, nullable=False)
    ctr: Mapped[float] = mapped_column(Float, default=0, nullable=False)
    average_position: Mapped[float] = mapped_column(Float, default=0, nullable=False)
    compare_clicks: Mapped[float | None] = mapped_column(Float, nullable=True)
    compare_impressions: Mapped[float | None] = mapped_column(Float, nullable=True)
    compare_ctr: Mapped[float | None] = mapped_column(Float, nullable=True)
    compare_position: Mapped[float | None] = mapped_column(Float, nullable=True)
    metrics_delta: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    row_key: Mapped[str] = mapped_column(String(512), nullable=False)
    data_source: Mapped[str] = mapped_column(String(32), default="search_console_api", nullable=False)


class SearchConsoleOpportunity(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "search_console_opportunities"

    sync_id: Mapped[UUID] = mapped_column(ForeignKey("search_console_syncs.id", ondelete="CASCADE"), index=True)
    organization_id: Mapped[UUID] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    site_url: Mapped[str] = mapped_column(String(2048), nullable=False)
    opportunity_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    rule_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    priority: Mapped[SearchConsoleOpportunityPriority] = mapped_column(String(16), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(16), default="open", nullable=False)
    query: Mapped[str | None] = mapped_column(Text, nullable=True)
    page_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    date_range_start: Mapped[date] = mapped_column(Date, nullable=False)
    date_range_end: Mapped[date] = mapped_column(Date, nullable=False)
    clicks: Mapped[float] = mapped_column(Float, default=0, nullable=False)
    impressions: Mapped[float] = mapped_column(Float, default=0, nullable=False)
    ctr: Mapped[float] = mapped_column(Float, default=0, nullable=False)
    average_position: Mapped[float] = mapped_column(Float, default=0, nullable=False)
    comparison_metrics: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    evidence: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    explanation: Mapped[str] = mapped_column(Text, nullable=False)
    dedupe_key: Mapped[str] = mapped_column(String(512), nullable=False)
