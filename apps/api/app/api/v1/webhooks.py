"""Webhook intake — signature validation required; never trust unverified payloads."""

from __future__ import annotations

import hashlib
import hmac
import json
import logging

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.db.session import get_db
from app.observability import events
from app.security.rate_limit import webhook_rate_limit
from app.services.lead_ingest_service import (
    MalformedWebhookError,
    WebhookProcessingError,
    ingest_meta_webhook,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/webhooks", tags=["webhooks"])


def _valid_signature(raw: bytes, signature: str | None, secret: str) -> bool:
    if not secret or not signature:
        return False
    digest = hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
    provided = signature.replace("sha256=", "")
    return hmac.compare_digest(digest, provided)


@router.post("/meta", dependencies=[Depends(webhook_rate_limit)])
async def meta_webhook(
    request: Request,
    x_hub_signature_256: str | None = Header(default=None),
    db: AsyncSession = Depends(get_db),
) -> dict:
    settings = get_settings()
    raw = await request.body()
    secret = settings.meta_app_secret
    if not secret:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="CREDENTIALS REQUIRED")
    if not _valid_signature(raw, x_hub_signature_256, secret):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid webhook signature")

    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Malformed JSON: {exc}") from exc

    try:
        outcome = await ingest_meta_webhook(db, payload)
    except MalformedWebhookError as exc:
        # Retrying will not help — tell Meta to stop.
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except WebhookProcessingError as exc:
        # Transient. A 5xx keeps the event in Meta's retry queue.
        logger.exception("Meta webhook processing failed")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Webhook processing failed; retry expected"
        ) from exc

    # Reported only after the transaction committed.
    events.webhook_received(
        provider="meta",
        event_id=None,
        outcome="processed",
        detail=(
            f"created={outcome.processed} duplicates={outcome.duplicates} "
            f"unroutable={outcome.unroutable} ambiguous={outcome.ambiguous}"
        ),
    )
    return {
        "received": True,
        "provider": "meta",
        "leads_created": outcome.processed,
        "duplicates_ignored": outcome.duplicates,
        "unroutable": outcome.unroutable,
        # Held back because more than one tenant claims the page. The raw event
        # is stored; no lead was created under a guessed organization.
        "ambiguous": outcome.ambiguous,
    }


@router.get("/meta")
async def meta_verify(hub_mode: str | None = None, hub_verify_token: str | None = None, hub_challenge: str | None = None):
    settings = get_settings()
    token = getattr(settings, "meta_webhook_verify_token", "") or ""
    if hub_mode == "subscribe" and token and hub_verify_token == token:
        return int(hub_challenge or 0)
    raise HTTPException(status_code=403, detail="Verification failed")
