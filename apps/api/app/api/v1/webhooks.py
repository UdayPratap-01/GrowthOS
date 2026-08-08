"""Webhook intake — signature validation required; never trust unverified payloads."""

from __future__ import annotations

import hashlib
import hmac

from fastapi import APIRouter, Header, HTTPException, Request, status

from app.core.config import get_settings

router = APIRouter(prefix="/webhooks", tags=["webhooks"])


def _valid_signature(raw: bytes, signature: str | None, secret: str) -> bool:
    if not secret or not signature:
        return False
    digest = hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
    provided = signature.replace("sha256=", "")
    return hmac.compare_digest(digest, provided)


@router.post("/meta")
async def meta_webhook(
    request: Request,
    x_hub_signature_256: str | None = Header(default=None),
) -> dict:
    settings = get_settings()
    raw = await request.body()
    secret = settings.meta_app_secret
    if not secret:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="CREDENTIALS REQUIRED")
    if not _valid_signature(raw, x_hub_signature_256, secret):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid webhook signature")
    # Verified payload accepted — processing hooks can enqueue jobs later.
    return {"received": True, "provider": "meta"}


@router.get("/meta")
async def meta_verify(hub_mode: str | None = None, hub_verify_token: str | None = None, hub_challenge: str | None = None):
    settings = get_settings()
    token = getattr(settings, "meta_webhook_verify_token", "") or ""
    if hub_mode == "subscribe" and token and hub_verify_token == token:
        return int(hub_challenge or 0)
    raise HTTPException(status_code=403, detail="Verification failed")
