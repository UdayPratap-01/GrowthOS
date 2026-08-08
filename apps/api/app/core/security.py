from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID

from jose import JWTError, jwt
from passlib.context import CryptContext

from app.core.config import get_settings

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(plain: str, hashed: str) -> bool:
    return pwd_context.verify(plain, hashed)


def create_token(subject: str, expires_delta: timedelta, token_type: str = "access") -> str:
    settings = get_settings()
    payload = {
        "sub": subject,
        "type": token_type,
        "exp": datetime.now(timezone.utc) + expires_delta,
        "iat": datetime.now(timezone.utc),
    }
    return jwt.encode(payload, settings.secret_key, algorithm=settings.algorithm)


def create_access_token(user_id: UUID) -> str:
    settings = get_settings()
    return create_token(str(user_id), timedelta(minutes=settings.access_token_expire_minutes), "access")


def create_refresh_token(user_id: UUID) -> str:
    settings = get_settings()
    return create_token(str(user_id), timedelta(days=settings.refresh_token_expire_days), "refresh")


def decode_token(token: str) -> dict[str, Any]:
    settings = get_settings()
    return jwt.decode(token, settings.secret_key, algorithms=[settings.algorithm])


def safe_decode_token(token: str) -> dict[str, Any] | None:
    try:
        return decode_token(token)
    except JWTError:
        return None
