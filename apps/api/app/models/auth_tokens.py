"""Server-side refresh token records.

A refresh token is only useful if it can be taken away. A self-contained JWT
cannot be: once signed it is valid until it expires, so logout is a suggestion
and a stolen token is good for its full lifetime. These rows are the authority —
the token is worthless without a live record here.

Only the SHA-256 of the token is stored. A leaked database dump therefore yields
no usable tokens, the same reason passwords are hashed.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import DateTime, ForeignKey, String
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class RefreshToken(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """
    One row per issued refresh token.

    `family_id` groups every token descended from a single login. Rotation
    creates a new row in the same family, so presenting an already-rotated token
    identifies the whole family as compromised and it can be revoked at once.
    """

    __tablename__ = "refresh_tokens"

    user_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    #: SHA-256 hex digest. Never the token itself.
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    family_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False, index=True)

    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    #: rotated | logout | logout_all | reuse_detected | password_change
    revoked_reason: Mapped[str | None] = mapped_column(String(32), nullable=True)
    replaced_by_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True)

    #: Coarse client fingerprint for incident review. Deliberately not an IP log.
    user_agent: Mapped[str | None] = mapped_column(String(255), nullable=True)
