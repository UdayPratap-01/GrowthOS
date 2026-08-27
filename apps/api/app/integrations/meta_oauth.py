"""Meta OAuth token lifecycle helpers — long-lived exchange + credential validation.

Mirrors Google's ensure_access_token pattern. Never logs access tokens or secrets.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.integrations.meta_family import META_GRAPH, META_TOKEN_URL
from app.integrations.persistence import load_tokens, upsert_integration
from app.models.ai_ops import Integration
from app.observability import events


def _safe_meta_error(text: str) -> str:
    """Strip anything that might look like a token from provider error text."""
    lowered = (text or "").lower()
    for needle in ("access_token", "app_secret", "client_secret", "fb_exchange_token"):
        if needle in lowered:
            return "Meta OAuth error (details redacted)"
    return (text or "")[:240]


async def exchange_for_long_lived_token(short_lived_token: str) -> dict[str, Any]:
    """
    Exchange a short-lived user token for a long-lived token (~60 days).

    Meta does not issue refresh_tokens for user access tokens; long-lived
    exchange is the supported renewal path.
    """
    settings = get_settings()
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(
            META_TOKEN_URL,
            params={
                "grant_type": "fb_exchange_token",
                "client_id": settings.meta_app_id,
                "client_secret": settings.meta_app_secret,
                "fb_exchange_token": short_lived_token,
            },
        )
    if resp.status_code >= 400:
        raise RuntimeError(f"Meta long-lived token exchange failed: {_safe_meta_error(resp.text)}")
    data = resp.json() if resp.content else {}
    if not data.get("access_token"):
        raise RuntimeError("Meta long-lived exchange returned no access_token")
    return data


async def discover_meta_ad_accounts(
    access_token: str,
    *,
    http_client: httpx.AsyncClient | None = None,
) -> list[dict[str, Any]]:
    """Read-only discovery of accessible Meta ad accounts (no secrets persisted)."""
    owns = http_client is None
    client = http_client or httpx.AsyncClient(timeout=30)
    try:
        resp = await client.get(
            f"{META_GRAPH}/me/adaccounts",
            params={
                "access_token": access_token,
                "fields": "id,name,account_id,account_status,currency,timezone_name",
                "limit": 50,
            },
        )
    finally:
        if owns:
            await client.aclose()
    if resp.status_code >= 400:
        raise RuntimeError(f"Meta adaccounts discovery failed: {_safe_meta_error(resp.text)}")
    raw = (resp.json() if resp.content else {}).get("data") or []
    out: list[dict[str, Any]] = []
    for acct in raw:
        if not isinstance(acct, dict):
            continue
        aid = acct.get("id") or (f"act_{acct['account_id']}" if acct.get("account_id") else None)
        if not aid:
            continue
        out.append(
            {
                "id": str(aid),
                "account_id": str(acct.get("account_id") or "").replace("act_", "") or None,
                "name": acct.get("name"),
                "status": acct.get("account_status"),
                "currency": acct.get("currency"),
                "timezone": acct.get("timezone_name"),
            }
        )
    return out


async def ensure_meta_access_token(
    db: AsyncSession,
    row: Integration,
    *,
    organization_id,
    client_id,
) -> str:
    """
    Return a usable Meta access token.

    If the stored token is near expiry and still looks short-lived, attempt
    long-lived exchange and re-persist. Never logs the token value.
    """
    tokens = load_tokens(row) or {}
    access = tokens.get("access_token")
    if not access:
        raise RuntimeError("No Meta access token available")

    obtained_at = tokens.get("obtained_at")
    expires_in = int(tokens.get("expires_in") or 0)
    long_lived = bool(tokens.get("long_lived"))

    stale = False
    if obtained_at and expires_in > 0:
        try:
            obtained = datetime.fromisoformat(str(obtained_at).replace("Z", "+00:00"))
            if obtained.tzinfo is None:
                obtained = obtained.replace(tzinfo=timezone.utc)
            # Refresh when within 24h of expiry for long-lived, or 5 min for short.
            cushion = 86400 if long_lived or expires_in > 86400 else 300
            stale = datetime.now(timezone.utc) >= obtained + timedelta(seconds=max(expires_in - cushion, 60))
        except ValueError:
            stale = True

    if access and not stale:
        return access

    # Attempt long-lived exchange (also renews near-expiry long-lived tokens when Meta allows).
    try:
        exchanged = await exchange_for_long_lived_token(access)
        new_payload = {
            **tokens,
            "access_token": exchanged["access_token"],
            "token_type": exchanged.get("token_type", tokens.get("token_type", "bearer")),
            "expires_in": exchanged.get("expires_in", expires_in or 5184000),
            "obtained_at": datetime.now(timezone.utc).isoformat(),
            "long_lived": True,
            "provider": "meta",
        }
        await upsert_integration(
            db,
            organization_id=organization_id,
            provider="meta",
            client_id=client_id,
            status=row.status or "connected",
            token_payload=new_payload,
        )
        events.integration_sync(
            provider="meta",
            organization_id=organization_id,
            success=True,
            message="long_lived_token_exchanged",
        )
        return new_payload["access_token"]
    except Exception as exc:
        events.integration_sync(
            provider="meta",
            organization_id=organization_id,
            success=False,
            message=type(exc).__name__,
        )
        # Fall back to existing token if still present — caller may get AUTH errors from Graph.
        if access and not stale:
            return access
        if access:
            return access
        raise


def build_meta_connection_config(
    *,
    me: dict[str, Any],
    ad_accounts: list[dict[str, Any]],
    display_name: str,
) -> dict[str, Any]:
    """Sanitized Integration.config for Meta — never includes tokens."""
    primary = ad_accounts[0] if ad_accounts else None
    return {
        "account_label": (primary or {}).get("name") or me.get("name") or me.get("id") or display_name,
        "meta_user_id": me.get("id"),
        # Prefer ad account act_* for canary allowlists; keep user id separately.
        "external_account_id": (primary or {}).get("id") or me.get("id"),
        "ad_accounts": [
            {
                "id": a.get("id"),
                "account_id": a.get("account_id"),
                "name": a.get("name"),
                "status": a.get("status"),
                "currency": a.get("currency"),
            }
            for a in ad_accounts[:50]
        ],
        "connected_at": datetime.now(timezone.utc).isoformat(),
        "token_type": "meta_user",
        "discovery": {
            "ad_account_count": len(ad_accounts),
            "discovered_at": datetime.now(timezone.utc).isoformat(),
        },
    }
