from datetime import date
from uuid import UUID

from sqlalchemy import Boolean, Date, Enum, ForeignKey, Integer, String, Text
from sqlalchemy import JSON
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.enums import Priority, RecommendationStatus


class AIRecommendation(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "ai_recommendations"

    organization_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    client_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True), ForeignKey("clients.id", ondelete="CASCADE"), nullable=True, index=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    problem: Mapped[str] = mapped_column(Text, nullable=False)
    evidence: Mapped[str] = mapped_column(Text, nullable=False)
    recommendation: Mapped[str] = mapped_column(Text, nullable=False)
    priority: Mapped[Priority] = mapped_column(Enum(Priority, name="recommendation_priority", native_enum=False), default=Priority.medium)
    expected_impact: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[RecommendationStatus] = mapped_column(
        Enum(RecommendationStatus, name="recommendation_status", native_enum=False), default=RecommendationStatus.pending, index=True
    )


class AIConversation(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "ai_conversations"

    organization_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    client_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True), ForeignKey("clients.id", ondelete="CASCADE"), nullable=True, index=True)
    agent: Mapped[str] = mapped_column(String(64), default="orchestrator")
    messages: Mapped[list] = mapped_column(JSON, default=list)


class Report(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "reports"

    organization_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    client_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("clients.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    period_start: Mapped[date] = mapped_column(Date, nullable=False)
    period_end: Mapped[date] = mapped_column(Date, nullable=False)
    content: Mapped[dict] = mapped_column(JSON, default=dict)
    export_path: Mapped[str | None] = mapped_column(String(512), nullable=True)
    status: Mapped[str] = mapped_column(String(64), default="draft")


class Integration(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "integrations"

    organization_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    client_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True), ForeignKey("clients.id", ondelete="CASCADE"), nullable=True, index=True)
    provider: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(64), default="not_connected")
    config: Mapped[dict] = mapped_column(JSON, default=dict)
    secret_ref: Mapped[str | None] = mapped_column(String(255), nullable=True)


class Notification(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "notifications"

    organization_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    is_read: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    meta: Mapped[dict] = mapped_column(JSON, default=dict)


class Subscription(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "subscriptions"

    organization_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), unique=True)
    plan: Mapped[str] = mapped_column(String(64), default="starter")
    status: Mapped[str] = mapped_column(String(64), default="active")
    seats: Mapped[int] = mapped_column(Integer, default=3, nullable=False)
    meta: Mapped[dict] = mapped_column(JSON, default=dict)


class AuditLog(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "audit_logs"

    organization_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True), ForeignKey("organizations.id", ondelete="SET NULL"), nullable=True, index=True)
    user_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    action: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
    resource_type: Mapped[str | None] = mapped_column(String(120), nullable=True)
    resource_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    ip_address: Mapped[str | None] = mapped_column(String(64), nullable=True)
    details: Mapped[dict] = mapped_column(JSON, default=dict)
