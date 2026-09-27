"""SEO audit and integration read endpoints (read-only)."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, HttpUrl
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import AuthContext, get_current_auth
from app.core.permissions import Permission, require_permission
from app.db.session import get_db
from app.schemas.seo_analysis import SeoFindingCompareOut, SeoFindingOut, SeoFindingSummaryOut
from app.schemas.seo_crawl import SeoCrawlCreateRequest, SeoCrawlOut, SeoCrawlPageOut
from app.security.limits import seo_crawl_limit
from app.services.seo_analysis_service import SeoAnalysisService
from app.services.seo_crawl_service import SeoCrawlService
from app.integrations.base import IntegrationConnectionStatus
from app.integrations.persistence import get_integration_row
from app.integrations.registry import get_integration
from app.publishing.capabilities import (
    google_search_console_capabilities,
    whatsapp_capabilities,
    youtube_capabilities,
)
from app.seo.audit import run_technical_seo_audit
from app.api.v1.seo_keywords import router as keywords_router
from app.api.v1.seo_search_console import router as search_console_router
from app.api.v1.seo_topics import router as topics_router
from app.api.v1.seo_competitors import router as competitors_router
from app.api.v1.seo_content_gaps import router as content_gaps_router
from app.api.v1.seo_recommendations import router as recommendations_router
from app.api.v1.seo_content_briefs import router as content_briefs_router
from app.api.v1.seo_content import router as seo_content_router
from app.api.v1.seo_actions import router as seo_actions_router

router = APIRouter(prefix="/seo", tags=["seo"])
router.include_router(search_console_router)
router.include_router(keywords_router)
router.include_router(topics_router)
router.include_router(competitors_router)
router.include_router(content_gaps_router)
router.include_router(recommendations_router)
router.include_router(content_briefs_router)
router.include_router(seo_content_router)
router.include_router(seo_actions_router)


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


@router.post("/crawls", response_model=SeoCrawlOut, status_code=status.HTTP_202_ACCEPTED, dependencies=[Depends(seo_crawl_limit)])
async def create_seo_crawl(
    body: SeoCrawlCreateRequest,
    auth: AuthContext = Depends(require_permission(Permission.read)),
    db: AsyncSession = Depends(get_db),
) -> SeoCrawlOut:
    """Queue a read-only full-site SEO crawl (async job)."""
    crawl = await SeoCrawlService(db).create_crawl(
        organization_id=auth.organization_id,
        user_id=auth.user_id,
        client_id=body.client_id,
        root_url=str(body.root_url),
        config={
            "max_pages": body.max_pages,
            "max_depth": body.max_depth,
            "request_timeout": body.request_timeout,
            "include_subdomains": body.include_subdomains,
        },
    )
    await db.commit()
    return SeoCrawlOut.model_validate(crawl)


@router.get("/crawls/{crawl_id}", response_model=SeoCrawlOut)
async def get_seo_crawl(
    crawl_id: UUID,
    auth: AuthContext = Depends(get_current_auth),
    db: AsyncSession = Depends(get_db),
) -> SeoCrawlOut:
    crawl = await SeoCrawlService(db).get_crawl(organization_id=auth.organization_id, crawl_id=crawl_id)
    return SeoCrawlOut.model_validate(crawl)


@router.get("/crawls/{crawl_id}/pages", response_model=list[SeoCrawlPageOut])
async def list_seo_crawl_pages(
    crawl_id: UUID,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    auth: AuthContext = Depends(get_current_auth),
    db: AsyncSession = Depends(get_db),
) -> list[SeoCrawlPageOut]:
    pages = await SeoCrawlService(db).list_pages(
        organization_id=auth.organization_id, crawl_id=crawl_id, limit=limit, offset=offset
    )
    return [SeoCrawlPageOut.model_validate(page) for page in pages]


@router.get("/crawls/{crawl_id}/findings", response_model=list[SeoFindingOut])
async def list_seo_findings(
    crawl_id: UUID,
    category: str | None = Query(default=None),
    severity: str | None = Query(default=None),
    status: str | None = Query(default=None, alias="status"),
    rule_id: str | None = Query(default=None),
    url: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    auth: AuthContext = Depends(get_current_auth),
    db: AsyncSession = Depends(get_db),
) -> list[SeoFindingOut]:
    findings = await SeoAnalysisService(db).list_findings(
        organization_id=auth.organization_id,
        crawl_id=crawl_id,
        category=category,
        severity=severity,
        status_filter=status,
        rule_id=rule_id,
        url=url,
        limit=limit,
        offset=offset,
    )
    await db.commit()
    return [SeoFindingOut.model_validate(row) for row in findings]


@router.get("/crawls/{crawl_id}/findings/compare", response_model=SeoFindingCompareOut)
async def compare_seo_findings(
    crawl_id: UUID,
    other_crawl_id: UUID = Query(...),
    auth: AuthContext = Depends(get_current_auth),
    db: AsyncSession = Depends(get_db),
) -> SeoFindingCompareOut:
    result = await SeoAnalysisService(db).compare(
        organization_id=auth.organization_id,
        crawl_id=crawl_id,
        other_crawl_id=other_crawl_id,
    )
    await db.commit()
    return SeoFindingCompareOut.model_validate(result)


@router.get("/crawls/{crawl_id}/findings/{finding_id}", response_model=SeoFindingOut)
async def get_seo_finding(
    crawl_id: UUID,
    finding_id: UUID,
    auth: AuthContext = Depends(get_current_auth),
    db: AsyncSession = Depends(get_db),
) -> SeoFindingOut:
    finding = await SeoAnalysisService(db).get_finding(
        organization_id=auth.organization_id,
        crawl_id=crawl_id,
        finding_id=finding_id,
    )
    return SeoFindingOut.model_validate(finding)


@router.get("/crawls/{crawl_id}/summary", response_model=SeoFindingSummaryOut)
async def get_seo_crawl_summary(
    crawl_id: UUID,
    auth: AuthContext = Depends(get_current_auth),
    db: AsyncSession = Depends(get_db),
) -> SeoFindingSummaryOut:
    summary = await SeoAnalysisService(db).summary(organization_id=auth.organization_id, crawl_id=crawl_id)
    await db.commit()
    return SeoFindingSummaryOut.model_validate(summary)


@router.post("/crawls/{crawl_id}/cancel", response_model=SeoCrawlOut)
async def cancel_seo_crawl(
    crawl_id: UUID,
    auth: AuthContext = Depends(require_permission(Permission.read)),
    db: AsyncSession = Depends(get_db),
) -> SeoCrawlOut:
    crawl = await SeoCrawlService(db).cancel_crawl(organization_id=auth.organization_id, crawl_id=crawl_id)
    await db.commit()
    return SeoCrawlOut.model_validate(crawl)


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
