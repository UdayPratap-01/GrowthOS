"""SEO schema artifact persistence (M9.11)."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSON
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class SeoSchemaArtifact(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "seo_schema_artifacts"
    __table_args__ = (
        UniqueConstraint("organization_id", "generation_key", name="uq_seo_schema_artifacts_org_gen_key"),
    )

    organization_id: Mapped[UUID] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    generated_content_id: Mapped[UUID] = mapped_column(
        ForeignKey("seo_generated_content.id", ondelete="CASCADE"), index=True
    )
    content_brief_id: Mapped[UUID] = mapped_column(ForeignKey("seo_content_briefs.id", ondelete="CASCADE"), index=True)
    schema_type: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(16), default="draft", nullable=False, index=True)
    json_ld: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    validation_status: Mapped[str] = mapped_column(String(16), default="not_generated", nullable=False, index=True)
    validation_errors: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    validation_warnings: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    eligibility_status: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    eligibility_reasons: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    evidence_refs: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    source_fields: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    limitations: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    generation_key: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    generation_algorithm_version: Mapped[str] = mapped_column(
        String(32), nullable=False, default="seo_schema_generation_v1", index=True
    )
    validation_algorithm_version: Mapped[str] = mapped_column(
        String(32), nullable=False, default="seo_schema_validation_v1", index=True
    )
    prompt_version: Mapped[str] = mapped_column(String(32), nullable=False, default="seo_schema_prompt_v1")
    provider: Mapped[str] = mapped_column(String(32), nullable=False, default="none")
    model: Mapped[str] = mapped_column(String(64), nullable=False, default="none")


class SeoSchemaFinding(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "seo_schema_findings"
    __table_args__ = (
        UniqueConstraint("organization_id", "artifact_id", "dedupe_key", name="uq_seo_schema_findings_artifact_dedupe"),
    )

    organization_id: Mapped[UUID] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    artifact_id: Mapped[UUID] = mapped_column(ForeignKey("seo_schema_artifacts.id", ondelete="CASCADE"), index=True)
    generated_content_id: Mapped[UUID] = mapped_column(
        ForeignKey("seo_generated_content.id", ondelete="CASCADE"), index=True
    )
    finding_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    category: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    severity: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    level: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    rationale: Mapped[str] = mapped_column(Text, nullable=False)
    property_path: Mapped[str | None] = mapped_column(String(255), nullable=True)
    evidence_refs: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    dedupe_key: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
