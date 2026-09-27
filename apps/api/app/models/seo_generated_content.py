"""SEO generated content persistence (M9.9)."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSON
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class SeoGeneratedContent(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "seo_generated_content"
    __table_args__ = (
        UniqueConstraint("organization_id", "generation_key", name="uq_seo_generated_content_org_gen_key"),
    )

    organization_id: Mapped[UUID] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    content_brief_id: Mapped[UUID] = mapped_column(ForeignKey("seo_content_briefs.id", ondelete="CASCADE"), index=True)
    recommendation_id: Mapped[UUID] = mapped_column(ForeignKey("seo_recommendations.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    slug: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    content_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(16), default="draft", nullable=False, index=True)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    structured_sections: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    primary_keyword: Mapped[str] = mapped_column(String(255), nullable=False)
    secondary_keywords: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    target_topic: Mapped[str | None] = mapped_column(String(512), nullable=True)
    target_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    meta_title: Mapped[str] = mapped_column(String(70), nullable=False)
    meta_description: Mapped[str] = mapped_column(String(160), nullable=False)
    outline_used: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    internal_link_targets: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    evidence_refs: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    limitations: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    brief_snapshot: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    model: Mapped[str] = mapped_column(String(64), nullable=False)
    prompt_version: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    algorithm_version: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    generation_key: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    word_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
