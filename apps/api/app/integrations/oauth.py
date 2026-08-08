"""Signed OAuth state helpers. Never put secrets in the frontend."""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from base64 import urlsafe_b64decode, urlsafe_b64encode
from uuid import UUID

from app.core.config import get_settings


def _sign(payload: bytes) -> str:
    key = get_settings().secret_key.encode("utf-8")
    return hmac.new(key, payload, hashlib.sha256).hexdigest()


def encode_oauth_state(*, provider: str, organization_id: UUID, client_id: UUID | None, user_id: UUID) -> str:
    body = {
        "provider": provider,
        "organization_id": str(organization_id),
        "client_id": str(client_id) if client_id else None,
        "user_id": str(user_id),
        "ts": int(time.time()),
    }
    raw = json.dumps(body, separators=(",", ":")).encode("utf-8")
    token = urlsafe_b64encode(raw).decode("utf-8").rstrip("=")
    return f"{token}.{_sign(raw)}"


def decode_oauth_state(state: str, *, max_age_seconds: int = 900) -> dict:
    try:
        token, signature = state.split(".", 1)
    except ValueError as exc:
        raise ValueError("Invalid OAuth state") from exc
    pad = "=" * (-len(token) % 4)
    raw = urlsafe_b64decode(token + pad)
    if not hmac.compare_digest(_sign(raw), signature):
        raise ValueError("Invalid OAuth state signature")
    body = json.loads(raw.decode("utf-8"))
    if int(time.time()) - int(body.get("ts", 0)) > max_age_seconds:
        raise ValueError("OAuth state expired")
    return body
