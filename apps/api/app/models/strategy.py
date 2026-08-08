from datetime import date
from uuid import UUID

from sqlalchemy import Date, Enum, ForeignKey, String, Text
from sqlalchemy import JSON
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.enums import ActionStatus, Priority


class Strategy(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "strategies"

    organization_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    client_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("clients.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    current_situation: Mapped[str] = mapped_column(Text, nullable=False)
    what_is_happening: Mapped[str] = mapped_column(Text, nullable=False)
    key_problems: Mapped[list] = mapped_column(JSON, default=list)
    opportunities: Mapped[list] = mapped_column(JSON, default=list)
    strategy_summary: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(64), default="draft")
    source: Mapped[str] = mapped_column(String(64), default="ai")
    context_snapshot: Mapped[dict] = mapped_column(JSON, default=dict)

    actions = relationship("StrategyAction", back_populates="strategy", cascade="all, delete-orphan")


class StrategyAction(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "strategy_actions"

    organization_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    client_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("clients.id", ondelete="CASCADE"), index=True)
    strategy_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("strategies.id", ondelete="CASCADE"), index=True)
    action: Mapped[str] = mapped_column(Text, nullable=False)
    channel: Mapped[str] = mapped_column(String(120), nullable=False)
    objective: Mapped[str] = mapped_column(String(255), nullable=False)
    priority: Mapped[Priority] = mapped_column(Enum(Priority, name="action_priority", native_enum=False), default=Priority.medium)
    estimated_effort: Mapped[str] = mapped_column(String(64), default="medium")
    expected_outcome: Mapped[str] = mapped_column(Text, nullable=False)
    required_assets: Mapped[list] = mapped_column(JSON, default=list)
    deadline: Mapped[date | None] = mapped_column(Date, nullable=True)
    status: Mapped[ActionStatus] = mapped_column(Enum(ActionStatus, name="action_status", native_enum=False), default=ActionStatus.pending, index=True)

    strategy = relationship("Strategy", back_populates="actions")
