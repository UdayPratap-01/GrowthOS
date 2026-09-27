"""SEO on-page optimization persistence (M9.10)."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSON
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class SeoOnPageOptimizationRun(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "seo_onpage_optimization_runs"
    __table_args__ = (
        UniqueConstraint("organization_id", "analysis_key", name="uq_seo_onpage_runs_org_analysis_key"),
    )

    organization_id: Mapped[UUID] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    generated_content_id: Mapped[UUID] = mapped_column(
        ForeignKey("seo_generated_content.id", ondelete="CASCADE"), index=True
    )
    content_brief_id: Mapped[UUID] = mapped_column(ForeignKey("seo_content_briefs.id", ondelete="CASCADE"), index=True)
    status: Mapped[str] = mapped_column(String(16), default="completed", nullable=False, index=True)
    analysis_key: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    stats: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    limitations: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    provider: Mapped[str] = mapped_column(String(32), nullable=False, default="none")
    model: Mapped[str] = mapped_column(String(64), nullable=False, default="none")
    prompt_version: Mapped[str] = mapped_column(String(32), nullable=False, default="seo_onpage_optimizer_prompt_v1")
    algorithm_version: Mapped[str] = mapped_column(
        String(32), nullable=False, default="seo_onpage_optimizer_v1", index=True
    )
    ai_enriched: Mapped[bool] = mapped_column(default=False, nullable=False)


class SeoOnPageFinding(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "seo_onpage_findings"
    __table_args__ = (
        UniqueConstraint("organization_id", "run_id", "dedupe_key", name="uq_seo_onpage_findings_run_dedupe"),
    )

    organization_id: Mapped[UUID] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    run_id: Mapped[UUID] = mapped_column(
        ForeignKey("seo_onpage_optimization_runs.id", ondelete="CASCADE"), index=True
    )
    generated_content_id: Mapped[UUID] = mapped_column(
        ForeignKey("seo_generated_content.id", ondelete="CASCADE"), index=True
    )
    content_brief_id: Mapped[UUID] = mapped_column(ForeignKey("seo_content_briefs.id", ondelete="CASCADE"), index=True)
    finding_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    category: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    severity: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    priority: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(16), default="open", nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    rationale: Mapped[str] = mapped_column(Text, nullable=False)
    current_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    expected_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    recommendation: Mapped[str] = mapped_column(Text, nullable=False)
    evidence_refs: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    affected_section: Mapped[str | None] = mapped_column(String(255), nullable=True)
    affected_element: Mapped[str | None] = mapped_column(String(64), nullable=True)
    suggested_change: Mapped[str | None] = mapped_column(Text, nullable=True)
    dedupe_key: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
