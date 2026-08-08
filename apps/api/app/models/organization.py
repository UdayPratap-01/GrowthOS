from uuid import UUID

from sqlalchemy import Boolean, Enum, ForeignKey, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.enums import MemberRole


class Organization(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "organizations"

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    slug: Mapped[str] = mapped_column(String(120), unique=True, index=True, nullable=False)
    demo_mode: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    plan: Mapped[str] = mapped_column(String(64), default="starter", nullable=False)

    members = relationship("OrganizationMember", back_populates="organization", cascade="all, delete-orphan")
    clients = relationship("Client", back_populates="organization", cascade="all, delete-orphan")


class OrganizationMember(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "organization_members"
    __table_args__ = (UniqueConstraint("organization_id", "user_id", name="uq_org_member"),)

    organization_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True)
    role: Mapped[MemberRole] = mapped_column(Enum(MemberRole, name="member_role", native_enum=False), default=MemberRole.member, nullable=False)

    organization = relationship("Organization", back_populates="members")
    user = relationship("User", back_populates="memberships")
