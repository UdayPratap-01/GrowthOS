"""SEO AI recommendation persistence (M9.7)."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import Float, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSON
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class SeoRecommendationRun(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "seo_recommendation_runs"
    __table_args__ = (
        UniqueConstraint("organization_id", "generation_key", name="uq_seo_recommendation_runs_org_gen_key"),
    )

    organization_id: Mapped[UUID] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    sync_id: Mapped[UUID | None] = mapped_column(ForeignKey("search_console_syncs.id", ondelete="SET NULL"), nullable=True, index=True)
    status: Mapped[str] = mapped_column(String(16), default="completed", nullable=False, index=True)
    generation_key: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    evidence_snapshot: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    stats: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    provider: Mapped[str] = mapped_column(String(32), nullable=False, default="unknown")
    model: Mapped[str] = mapped_column(String(64), nullable=False, default="unknown")
    prompt_version: Mapped[str] = mapped_column(String(32), nullable=False, default="seo_recommendation_prompt_v1")
    algorithm_version: Mapped[str] = mapped_column(String(32), nullable=False, default="seo_recommendation_v1", index=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)


class SeoRecommendation(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "seo_recommendations"
    __table_args__ = (
        UniqueConstraint("organization_id", "dedupe_key", name="uq_seo_recommendations_org_dedupe"),
    )

    organization_id: Mapped[UUID] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    run_id: Mapped[UUID] = mapped_column(ForeignKey("seo_recommendation_runs.id", ondelete="CASCADE"), index=True)
    sync_id: Mapped[UUID | None] = mapped_column(ForeignKey("search_console_syncs.id", ondelete="SET NULL"), nullable=True, index=True)
    recommendation_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    rationale: Mapped[str] = mapped_column(Text, nullable=False)
    priority: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    impact: Mapped[str] = mapped_column(String(16), nullable=False)
    effort: Mapped[str] = mapped_column(String(16), nullable=False)
    confidence: Mapped[float] = mapped_column(Float, default=0, nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="open", nullable=False, index=True)
    evidence_refs: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    affected_urls: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    affected_keywords: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    affected_topics: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    competitor_context: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    recommended_action: Mapped[str] = mapped_column(Text, nullable=False)
    expected_outcome: Mapped[str] = mapped_column(Text, nullable=False)
    limitations: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    model: Mapped[str] = mapped_column(String(64), nullable=False)
    prompt_version: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    algorithm_version: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    generation_key: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    dedupe_key: Mapped[str] = mapped_column(String(128), nullable=False)
