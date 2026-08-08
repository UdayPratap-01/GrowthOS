"""Shared Google OAuth helpers for Analytics, Ads, and YouTube."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.integrations.persistence import load_tokens, upsert_integration
from app.models.ai_ops import Integration

GOOGLE_AUTH = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN = "https://oauth2.googleapis.com/token"


async def exchange_code(*, code: str, redirect_uri: str) -> dict[str, Any]:
    settings = get_settings()
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            GOOGLE_TOKEN,
            data={
                "code": code,
                "client_id": settings.google_client_id,
                "client_secret": settings.google_client_secret,
                "redirect_uri": redirect_uri,
                "grant_type": "authorization_code",
            },
        )
    if resp.status_code >= 400:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Google token exchange failed: {resp.text}",
        )
    data = resp.json()
    if not data.get("access_token"):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No Google access token returned")
    return data


async def refresh_access_token(refresh_token: str) -> dict[str, Any]:
    settings = get_settings()
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            GOOGLE_TOKEN,
            data={
                "client_id": settings.google_client_id,
                "client_secret": settings.google_client_secret,
                "refresh_token": refresh_token,
                "grant_type": "refresh_token",
            },
        )
    if resp.status_code >= 400:
        raise RuntimeError(f"Google token refresh failed: {resp.text}")
    data = resp.json()
    if not data.get("access_token"):
        raise RuntimeError("Google token refresh returned no access_token")
    return data


async def ensure_access_token(
    db: AsyncSession,
    row: Integration,
    *,
    organization_id,
    provider: str,
    client_id,
) -> str:
    """Return a usable access token, refreshing and re-persisting when expired."""
    tokens = load_tokens(row) or {}
    access = tokens.get("access_token")
    refresh = tokens.get("refresh_token")
    obtained_at = tokens.get("obtained_at")
    expires_in = int(tokens.get("expires_in") or 3600)

    stale = True
    if access and obtained_at:
        try:
            obtained = datetime.fromisoformat(obtained_at)
            stale = datetime.now(timezone.utc) >= obtained + timedelta(seconds=max(expires_in - 120, 60))
        except ValueError:
            stale = True
    elif access and not refresh:
        stale = False

    if access and not stale:
        return access
    if not refresh:
        if access:
            return access
        raise RuntimeError("No Google access or refresh token available")

    refreshed = await refresh_access_token(refresh)
    new_payload = {
        **tokens,
        "access_token": refreshed["access_token"],
        "expires_in": refreshed.get("expires_in", expires_in),
        "token_type": refreshed.get("token_type", "Bearer"),
        "obtained_at": datetime.now(timezone.utc).isoformat(),
        "provider": provider,
    }
    if refreshed.get("refresh_token"):
        new_payload["refresh_token"] = refreshed["refresh_token"]
    await upsert_integration(
        db,
        organization_id=organization_id,
        provider=provider,
        client_id=client_id,
        status=row.status,
        token_payload=new_payload,
    )
    return new_payload["access_token"]
