"""SEO weekly report models (M9.16)."""

from __future__ import annotations

from datetime import date, datetime
from uuid import UUID

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Index, String, Text, UniqueConstraint
from sqlalchemy import JSON
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class SeoReportConfig(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "seo_report_config"
    __table_args__ = (
        UniqueConstraint("organization_id", name="uq_seo_report_config_org"),
    )

    organization_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), index=True
    )
    reporting_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    last_report_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_failure_reason: Mapped[str | None] = mapped_column(String(512), nullable=True)


class SeoWeeklyReport(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "seo_weekly_reports"
    __table_args__ = (
        UniqueConstraint(
            "organization_id",
            "period_start",
            "period_end",
            "report_version",
            name="uq_seo_weekly_reports_org_period_version",
        ),
        Index("ix_seo_weekly_reports_org_created", "organization_id", "created_at"),
        Index("ix_seo_weekly_reports_org_status", "organization_id", "status"),
    )

    organization_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), index=True
    )
    period_start: Mapped[date] = mapped_column(Date, nullable=False)
    period_end: Mapped[date] = mapped_column(Date, nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="queued", index=True)
    report_version: Mapped[str] = mapped_column(String(16), default="v1")
    generated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    data_freshness: Mapped[dict] = mapped_column(JSON, default=dict)
    summary: Mapped[str] = mapped_column(Text, default="")
    report_payload: Mapped[dict] = mapped_column(JSON, default=dict)
    limitations: Mapped[list] = mapped_column(JSON, default=list)
    error_message: Mapped[str | None] = mapped_column(String(512), nullable=True)
    background_job_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True)
    generation_key: Mapped[str] = mapped_column(String(128), index=True)
