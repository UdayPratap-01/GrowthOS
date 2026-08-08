from datetime import datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import DateTime, Enum, ForeignKey, Numeric, String, Text, UniqueConstraint
from sqlalchemy import JSON
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.enums import ClientStatus, MemberRole


class Client(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "clients"

    organization_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), index=True, nullable=False
    )
    business_name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    industry: Mapped[str | None] = mapped_column(String(120), nullable=True)
    website: Mapped[str | None] = mapped_column(String(512), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    location: Mapped[str | None] = mapped_column(String(255), nullable=True)
    target_audience: Mapped[str | None] = mapped_column(Text, nullable=True)
    products_services: Mapped[str | None] = mapped_column(Text, nullable=True)
    marketing_goals: Mapped[str | None] = mapped_column(Text, nullable=True)
    monthly_budget: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    brand_voice: Mapped[str | None] = mapped_column(Text, nullable=True)
    competitors: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    primary_channels: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    kpis: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    status: Mapped[ClientStatus] = mapped_column(
        Enum(ClientStatus, name="client_status", native_enum=False), default=ClientStatus.active, nullable=False, index=True
    )
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    organization = relationship("Organization", back_populates="clients")
    users = relationship("ClientUser", back_populates="client", cascade="all, delete-orphan")


class ClientUser(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "client_users"
    __table_args__ = (UniqueConstraint("client_id", "user_id", name="uq_client_user"),)

    organization_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    client_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("clients.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True)
    role: Mapped[MemberRole] = mapped_column(Enum(MemberRole, name="client_user_role", native_enum=False), default=MemberRole.member)

    client = relationship("Client", back_populates="users")
