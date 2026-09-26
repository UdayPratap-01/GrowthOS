"""SEO audit and integration read endpoints (read-only)."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, HttpUrl
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import AuthContext, get_current_auth
from app.core.permissions import Permission, require_permission
from app.db.session import get_db
from app.integrations.base import IntegrationConnectionStatus
from app.integrations.persistence import get_integration_row
from app.integrations.registry import get_integration
from app.publishing.capabilities import (
    google_search_console_capabilities,
    whatsapp_capabilities,
    youtube_capabilities,
)
from app.seo.audit import run_technical_seo_audit

router = APIRouter(prefix="/seo", tags=["seo"])


class SeoAuditRequest(BaseModel):
    url: HttpUrl


@router.post("/audit")
async def seo_audit(
    body: SeoAuditRequest,
    auth: AuthContext = Depends(require_permission(Permission.read)),
) -> dict:
    """Run a technical SEO audit on a URL (crawl observations — not index verification)."""
    result = await run_technical_seo_audit(str(body.url))
    return result.as_dict()


@router.get("/integrations/{provider}/capabilities")
async def integration_capabilities(
    provider: str,
    client_id: UUID | None = Query(default=None),
    auth: AuthContext = Depends(get_current_auth),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Return read-only capability matrix for a provider."""
    integration = get_integration(provider)
    if not integration:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Unknown integration provider")
    integration._db = db  # type: ignore[attr-defined]
    status_row = await integration.get_connection_status(auth.organization_id, client_id)
    connected = status_row.status == IntegrationConnectionStatus.connected
    configured = status_row.credentials_configured

    if provider == "youtube":
        matrix = youtube_capabilities(connected=connected, credentials_configured=configured)
    elif provider == "whatsapp":
        matrix = whatsapp_capabilities(connected=connected, credentials_configured=configured)
    elif provider == "google_search_console":
        matrix = google_search_console_capabilities(connected=connected, credentials_configured=configured)
    else:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Capabilities not exposed for this provider")

    return matrix.as_dict()


@router.get("/integrations/youtube/videos")
async def youtube_videos(
    client_id: UUID | None = Query(default=None),
    auth: AuthContext = Depends(get_current_auth),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Return cached read-only YouTube video metadata from last sync."""
    row = await get_integration_row(
        db, organization_id=auth.organization_id, provider="youtube", client_id=client_id
    )
    if not row or row.status != "connected":
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="YouTube not connected")
    cfg = row.config or {}
    return {
        "channel_id": cfg.get("channel_id"),
        "account_label": cfg.get("account_label"),
        "channel_stats": cfg.get("channel_stats") or {},
        "videos": cfg.get("recent_videos") or [],
        "source": "youtube_sync_cache",
        "read_only": True,
    }


@router.get("/integrations/whatsapp/status")
async def whatsapp_status(
    client_id: UUID | None = Query(default=None),
    auth: AuthContext = Depends(get_current_auth),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Return read-only WhatsApp Business metadata from last sync."""
    row = await get_integration_row(
        db, organization_id=auth.organization_id, provider="whatsapp", client_id=client_id
    )
    if not row:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="WhatsApp not connected")
    cfg = row.config or {}
    return {
        "status": row.status,
        "account_label": cfg.get("account_label"),
        "whatsapp_business_accounts": cfg.get("whatsapp_business_accounts") or [],
        "phone_numbers": cfg.get("phone_numbers") or [],
        "webhook_status": cfg.get("webhook_status") or "PENDING_PUBLIC_HTTPS",
        "read_only": True,
        "messaging_enabled": False,
    }


@router.get("/integrations/google_search_console/analytics")
async def search_console_analytics(
    client_id: UUID | None = Query(default=None),
    auth: AuthContext = Depends(get_current_auth),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Return cached Search Console analytics from last sync."""
    row = await get_integration_row(
        db,
        organization_id=auth.organization_id,
        provider="google_search_console",
        client_id=client_id,
    )
    if not row or row.status != "connected":
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Search Console not connected")
    cfg = row.config or {}
    analytics = cfg.get("last_search_analytics") or {}
    return {
        "site_url": cfg.get("site_url"),
        "sites": cfg.get("sites") or [],
        "analytics": analytics,
        "synced_at": cfg.get("analytics_synced_at"),
        "source": "search_console_api",
        "read_only": True,
    }
