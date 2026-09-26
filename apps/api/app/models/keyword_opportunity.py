"""Keyword opportunity persistence (M9.4)."""

from __future__ import annotations

from datetime import date
from uuid import UUID

from sqlalchemy import Date, Float, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import JSON
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.enums import KeywordOpportunityPriority


class KeywordOpportunity(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "keyword_opportunities"

    sync_id: Mapped[UUID] = mapped_column(ForeignKey("search_console_syncs.id", ondelete="CASCADE"), index=True)
    organization_id: Mapped[UUID] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    site_url: Mapped[str] = mapped_column(String(2048), nullable=False, index=True)
    query: Mapped[str] = mapped_column(Text, nullable=False)
    normalized_query: Mapped[str] = mapped_column(String(1024), nullable=False, index=True)
    page_url: Mapped[str | None] = mapped_column(String(2048), nullable=True, index=True)
    opportunity_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    rule_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(16), default="open", nullable=False, index=True)
    priority: Mapped[KeywordOpportunityPriority] = mapped_column(String(16), nullable=False, index=True)
    priority_score: Mapped[float] = mapped_column(Float, default=0, nullable=False)
    clicks: Mapped[float] = mapped_column(Float, default=0, nullable=False)
    impressions: Mapped[float] = mapped_column(Float, default=0, nullable=False)
    ctr: Mapped[float] = mapped_column(Float, default=0, nullable=False)
    average_position: Mapped[float] = mapped_column(Float, default=0, nullable=False)
    previous_clicks: Mapped[float | None] = mapped_column(Float, nullable=True)
    previous_impressions: Mapped[float | None] = mapped_column(Float, nullable=True)
    previous_ctr: Mapped[float | None] = mapped_column(Float, nullable=True)
    previous_average_position: Mapped[float | None] = mapped_column(Float, nullable=True)
    change_metrics: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    query_classification: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    page_associations: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    evidence: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    explanation: Mapped[str] = mapped_column(Text, nullable=False)
    date_start: Mapped[date] = mapped_column(Date, nullable=False)
    date_end: Mapped[date] = mapped_column(Date, nullable=False)
    dedupe_key: Mapped[str] = mapped_column(String(512), nullable=False)
    data_source: Mapped[str] = mapped_column(String(32), default="search_console_performance", nullable=False)
