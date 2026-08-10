"""
Refresh token lifecycle: issue, rotate, revoke, detect reuse.

Why opaque tokens rather than JWTs
----------------------------------
The previous refresh token was a signed JWT, which cannot be revoked: logout
could delete it from the browser but the token itself stayed valid until it
expired. These tokens are random strings whose SHA-256 is stored, so validity is
a database question and logout actually ends the session.

Rotation and reuse detection
----------------------------
Each refresh consumes the presented token and issues a new one in the same
*family*. A token therefore has exactly one legitimate use. If a token that was
already rotated is presented again, either it was stolen and replayed, or the
legitimate client is replaying — and neither can be distinguished from the other,
so the whole family is revoked and the user must log in again. Losing a session
is the correct trade against letting a thief keep one.
"""

from __future__ import annotations

import hashlib
import logging
import secrets
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.security import create_access_token
from app.models.auth_tokens import RefreshToken
from app.models.user import User
from app.schemas.auth import TokenResponse

logger = logging.getLogger("growthos.auth")

#: 48 bytes from the system CSPRNG, url-safe encoded.
TOKEN_BYTES = 48


class RefreshTokenError(Exception):
    """Refresh failed. The reason is for the log, never for the response."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def hash_token(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(value: datetime | None) -> datetime | None:
    """SQLite hands back naive datetimes; treat stored values as UTC."""
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


class RefreshTokenService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    # -- issue ------------------------------------------------------------

    async def issue(
        self,
        user_id: UUID,
        *,
        family_id: UUID | None = None,
        user_agent: str | None = None,
    ) -> tuple[str, RefreshToken]:
        settings = get_settings()
        raw = secrets.token_urlsafe(TOKEN_BYTES)
        record = RefreshToken(
            user_id=user_id,
            token_hash=hash_token(raw),
            family_id=family_id or uuid4(),
            expires_at=_now() + timedelta(days=settings.refresh_token_expire_days),
            user_agent=(user_agent or "")[:255] or None,
        )
        self.db.add(record)
        await self.db.flush()
        return raw, record

    async def issue_pair(
        self, user_id: UUID, *, family_id: UUID | None = None, user_agent: str | None = None
    ) -> TokenResponse:
        raw, _ = await self.issue(user_id, family_id=family_id, user_agent=user_agent)
        return TokenResponse(access_token=create_access_token(user_id), refresh_token=raw)

    # -- rotate -----------------------------------------------------------

    async def rotate(self, raw_token: str, *, user_agent: str | None = None) -> TokenResponse:
        """Consume a refresh token and issue its successor."""
        record = await self.db.scalar(
            select(RefreshToken).where(RefreshToken.token_hash == hash_token(raw_token))
        )
        if record is None:
            raise RefreshTokenError("unknown_token")

        if record.revoked_at is not None:
            # A token that was rotated and is now being presented again means the
            # token was captured, or a client is replaying. Both are handled the
            # same way: end every session descended from this login.
            if record.revoked_reason == "rotated":
                await self.revoke_family(record.family_id, reason="reuse_detected")
                logger.warning(
                    "Refresh token reuse detected; family revoked",
                    extra={
                        "event": "auth.refresh_reuse",
                        "auth_user_id": str(record.user_id),
                        "family": str(record.family_id),
                    },
                )
                raise RefreshTokenError("reuse_detected")
            raise RefreshTokenError("revoked")

        expires_at = _aware(record.expires_at)
        if expires_at is not None and expires_at <= _now():
            raise RefreshTokenError("expired")

        user = await self.db.get(User, record.user_id)
        if user is None or not user.is_active:
            # Deactivating an account must not leave a working refresh token.
            await self.revoke_family(record.family_id, reason="logout_all")
            raise RefreshTokenError("inactive_user")

        raw, successor = await self.issue(
            record.user_id, family_id=record.family_id, user_agent=user_agent
        )
        now = _now()
        record.revoked_at = now
        record.revoked_reason = "rotated"
        record.used_at = now
        record.replaced_by_id = successor.id
        await self.db.flush()

        logger.info(
            "Refresh token rotated",
            extra={"event": "auth.refresh", "auth_user_id": str(record.user_id)},
        )
        return TokenResponse(access_token=create_access_token(record.user_id), refresh_token=raw)

    # -- revoke -----------------------------------------------------------

    async def revoke(self, raw_token: str, *, reason: str = "logout") -> bool:
        record = await self.db.scalar(
            select(RefreshToken).where(RefreshToken.token_hash == hash_token(raw_token))
        )
        if record is None or record.revoked_at is not None:
            # Logout is idempotent and does not report whether the token existed.
            return False
        record.revoked_at = _now()
        record.revoked_reason = reason
        await self.db.flush()
        return True

    async def revoke_family(self, family_id: UUID, *, reason: str) -> int:
        result = await self.db.execute(
            update(RefreshToken)
            .where(RefreshToken.family_id == family_id, RefreshToken.revoked_at.is_(None))
            .values(revoked_at=_now(), revoked_reason=reason)
        )
        await self.db.flush()
        return int(result.rowcount or 0)

    async def revoke_all_for_user(self, user_id: UUID, *, reason: str = "logout_all") -> int:
        result = await self.db.execute(
            update(RefreshToken)
            .where(RefreshToken.user_id == user_id, RefreshToken.revoked_at.is_(None))
            .values(revoked_at=_now(), revoked_reason=reason)
        )
        await self.db.flush()
        return int(result.rowcount or 0)

    # -- housekeeping -----------------------------------------------------

    async def purge_expired(self, *, older_than_days: int = 30) -> int:
        """Expired rows prove nothing after the audit window; drop them."""
        from sqlalchemy import delete

        cutoff = _now() - timedelta(days=older_than_days)
        result = await self.db.execute(
            delete(RefreshToken).where(RefreshToken.expires_at < cutoff)
        )
        await self.db.flush()
        return int(result.rowcount or 0)

    async def active_sessions(self, user_id: UUID) -> list[RefreshToken]:
        rows = await self.db.scalars(
            select(RefreshToken)
            .where(
                RefreshToken.user_id == user_id,
                RefreshToken.revoked_at.is_(None),
                RefreshToken.expires_at > _now(),
            )
            .order_by(RefreshToken.created_at.desc())
        )
        return list(rows)
