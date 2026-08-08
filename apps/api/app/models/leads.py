from datetime import datetime
from uuid import UUID

from sqlalchemy import DateTime, Enum, ForeignKey, Integer, String, Text
from sqlalchemy import JSON
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.enums import LeadStatus


class Lead(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "leads"

    organization_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    client_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("clients.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    email: Mapped[str | None] = mapped_column(String(320), nullable=True, index=True)
    phone: Mapped[str | None] = mapped_column(String(64), nullable=True)
    source: Mapped[str | None] = mapped_column(String(120), nullable=True)
    campaign: Mapped[str | None] = mapped_column(String(255), nullable=True)
    ad: Mapped[str | None] = mapped_column(String(255), nullable=True)
    lead_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    score_explanation: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[LeadStatus] = mapped_column(Enum(LeadStatus, name="lead_status", native_enum=False), default=LeadStatus.new, index=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_activity_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    activities = relationship("LeadActivity", back_populates="lead", cascade="all, delete-orphan")


class LeadActivity(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "lead_activities"

    organization_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    client_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("clients.id", ondelete="CASCADE"), index=True)
    lead_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("leads.id", ondelete="CASCADE"), index=True)
    activity_type: Mapped[str] = mapped_column(String(120), nullable=False)
    body: Mapped[str | None] = mapped_column(Text, nullable=True)
    meta: Mapped[dict] = mapped_column(JSON, default=dict)

    lead = relationship("Lead", back_populates="activities")
