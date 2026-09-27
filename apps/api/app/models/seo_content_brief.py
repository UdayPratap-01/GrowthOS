"""SEO content brief persistence (M9.8)."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSON
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class SeoContentBrief(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "seo_content_briefs"
    __table_args__ = (
        UniqueConstraint("organization_id", "generation_key", name="uq_seo_content_briefs_org_gen_key"),
    )

    organization_id: Mapped[UUID] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    recommendation_id: Mapped[UUID] = mapped_column(ForeignKey("seo_recommendations.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    brief_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    primary_keyword: Mapped[str] = mapped_column(String(255), nullable=False)
    secondary_keywords: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    target_topic: Mapped[str | None] = mapped_column(String(512), nullable=True)
    search_intent: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    target_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    content_goal: Mapped[str] = mapped_column(Text, nullable=False)
    target_audience: Mapped[str] = mapped_column(Text, nullable=False)
    suggested_content_type: Mapped[str] = mapped_column(String(128), nullable=False)
    suggested_angle: Mapped[str] = mapped_column(Text, nullable=False)
    outline: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    questions_to_answer: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    entities_to_cover: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    internal_link_targets: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    competitor_context: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    evidence_refs: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    source_recommendation_ids: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    content_requirements: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    seo_requirements: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    limitations: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    context_snapshot: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="draft", nullable=False, index=True)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    model: Mapped[str] = mapped_column(String(64), nullable=False)
    prompt_version: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    algorithm_version: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    generation_key: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
