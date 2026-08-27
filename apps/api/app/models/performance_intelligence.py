"""Analysis-only performance recommendations (never execute external mutations)."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import Date, DateTime, Enum, ForeignKey, Index, Integer, Numeric, String, Text, UniqueConstraint
from sqlalchemy import JSON
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.enums import PerformanceRecommendationStatus


class PerformanceRecommendation(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """
    Structured marketing performance recommendation.

    APPROVED here means a human reviewed the analysis — it does NOT execute
    Meta/Google mutations. Execution belongs to a later milestone via AIAction.
    """

    __tablename__ = "performance_recommendations"
    __table_args__ = (
        UniqueConstraint(
            "organization_id",
            "fingerprint",
            name="uq_perf_recommendation_org_fingerprint",
        ),
        Index("ix_perf_rec_org_status", "organization_id", "status"),
        Index("ix_perf_rec_org_platform", "organization_id", "platform"),
        Index("ix_perf_rec_org_client", "organization_id", "client_id"),
        Index("ix_perf_rec_org_created", "organization_id", "created_at"),
    )

    organization_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), index=True
    )
    client_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("clients.id", ondelete="CASCADE"), nullable=True, index=True
    )
    platform: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    entity_level: Mapped[str] = mapped_column(String(32), nullable=False, default="campaign")
    external_account_id: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    external_campaign_id: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    external_ad_set_id: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    external_ad_id: Mapped[str] = mapped_column(String(255), nullable=False, default="")

    recommendation_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    severity: Mapped[str] = mapped_column(String(16), nullable=False, default="MEDIUM")
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    explanation: Mapped[str] = mapped_column(Text, nullable=False)
    evidence: Mapped[list] = mapped_column(JSON, default=list)
    affected_metrics: Mapped[list] = mapped_column(JSON, default=list)
    current_values: Mapped[dict] = mapped_column(JSON, default=dict)
    comparison_values: Mapped[dict] = mapped_column(JSON, default=dict)
    percentage_changes: Mapped[dict] = mapped_column(JSON, default=dict)
    confidence: Mapped[Decimal] = mapped_column(Numeric(5, 4), nullable=False, default=0)
    suggested_action: Mapped[dict] = mapped_column(JSON, default=dict)
    signal_category: Mapped[str] = mapped_column(String(32), nullable=False, default="UNDERPERFORMANCE")
    analysis_window_days: Mapped[int] = mapped_column(Integer, nullable=False, default=7)
    window_current_start: Mapped[date] = mapped_column(Date, nullable=False)
    window_current_end: Mapped[date] = mapped_column(Date, nullable=False)
    window_previous_start: Mapped[date] = mapped_column(Date, nullable=False)
    window_previous_end: Mapped[date] = mapped_column(Date, nullable=False)
    #: Deterministic hash for idempotent persistence within an analysis window.
    fingerprint: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[PerformanceRecommendationStatus] = mapped_column(
        Enum(
            PerformanceRecommendationStatus,
            name="performance_recommendation_status",
            native_enum=False,
        ),
        default=PerformanceRecommendationStatus.new,
        index=True,
    )
    explanation_source: Mapped[str] = mapped_column(String(32), nullable=False, default="deterministic")
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
