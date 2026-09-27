"""SEO internal-link engine persistence (M9.12)."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import Boolean, Float, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSON
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class SeoInternalLinkRun(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "seo_internal_link_runs"
    __table_args__ = (
        UniqueConstraint("organization_id", "analysis_key", name="uq_seo_internal_link_runs_org_analysis_key"),
    )

    organization_id: Mapped[UUID] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    generated_content_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("seo_generated_content.id", ondelete="CASCADE"), nullable=True, index=True
    )
    content_brief_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("seo_content_briefs.id", ondelete="CASCADE"), nullable=True, index=True
    )
    crawl_id: Mapped[UUID | None] = mapped_column(ForeignKey("seo_crawls.id", ondelete="SET NULL"), nullable=True, index=True)
    status: Mapped[str] = mapped_column(String(16), default="completed", nullable=False, index=True)
    analysis_key: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    stats: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    limitations: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    provider: Mapped[str] = mapped_column(String(32), default="none", nullable=False)
    model: Mapped[str] = mapped_column(String(64), default="none", nullable=False)
    prompt_version: Mapped[str] = mapped_column(String(32), default="internal_link_prompt_v1", nullable=False)
    algorithm_version: Mapped[str] = mapped_column(String(32), default="internal_link_engine_v1", nullable=False, index=True)
    ai_enriched: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)


class SeoInternalLinkOpportunity(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "seo_internal_link_opportunities"
    __table_args__ = (
        UniqueConstraint("organization_id", "run_id", "dedupe_key", name="uq_seo_internal_link_opps_run_dedupe"),
    )

    organization_id: Mapped[UUID] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    run_id: Mapped[UUID] = mapped_column(ForeignKey("seo_internal_link_runs.id", ondelete="CASCADE"), index=True)
    generated_content_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("seo_generated_content.id", ondelete="CASCADE"), nullable=True, index=True
    )
    content_brief_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("seo_content_briefs.id", ondelete="CASCADE"), nullable=True, index=True
    )
    source_url: Mapped[str] = mapped_column(String(2048), nullable=False, index=True)
    target_url: Mapped[str] = mapped_column(String(2048), nullable=False, index=True)
    source_crawl_page_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("seo_crawl_pages.id", ondelete="SET NULL"), nullable=True, index=True
    )
    target_crawl_page_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("seo_crawl_pages.id", ondelete="SET NULL"), nullable=True, index=True
    )
    anchor_text: Mapped[str] = mapped_column(String(255), nullable=False)
    anchor_alternatives: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    opportunity_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    relationship_reason: Mapped[str] = mapped_column(Text, nullable=False)
    source_topic: Mapped[str | None] = mapped_column(String(255), nullable=True)
    target_topic: Mapped[str | None] = mapped_column(String(255), nullable=True)
    source_keywords: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    target_keywords: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    relevance_score: Mapped[float] = mapped_column(Float, nullable=False, index=True)
    confidence: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    score_breakdown: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    evidence_refs: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    limitations: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="suggested", nullable=False, index=True)
    dedupe_key: Mapped[str] = mapped_column(String(256), nullable=False, index=True)
