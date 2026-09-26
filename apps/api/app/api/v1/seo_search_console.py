"""Search Console intelligence API (M9.3)."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import AuthContext, get_current_auth
from app.core.permissions import Permission, require_permission
from app.db.session import get_db
from app.schemas.search_console import (
    SearchConsoleOpportunityOut,
    SearchConsolePerformanceOut,
    SearchConsolePropertyOut,
    SearchConsoleSummaryOut,
    SearchConsoleSyncOut,
    SearchConsoleSyncRequest,
)
from app.security.limits import gsc_sync_limit
from app.services.search_console_intelligence_service import SearchConsoleIntelligenceService

router = APIRouter(prefix="/search-console", tags=["seo-search-console"])


@router.get("/properties", response_model=list[SearchConsolePropertyOut])
async def list_gsc_properties(
    client_id: UUID | None = Query(default=None),
    auth: AuthContext = Depends(get_current_auth),
    db: AsyncSession = Depends(get_db),
) -> list[SearchConsolePropertyOut]:
    props = await SearchConsoleIntelligenceService(db).list_properties(
        organization_id=auth.organization_id, client_id=client_id
    )
    return [SearchConsolePropertyOut.model_validate(p) for p in props]


@router.post("/sync", response_model=SearchConsoleSyncOut, dependencies=[Depends(gsc_sync_limit)])
async def sync_search_console(
    body: SearchConsoleSyncRequest,
    auth: AuthContext = Depends(require_permission(Permission.read)),
    db: AsyncSession = Depends(get_db),
) -> SearchConsoleSyncOut:
    sync = await SearchConsoleIntelligenceService(db).run_sync(
        organization_id=auth.organization_id,
        client_id=body.client_id,
        preset=body.preset or "last_28_days",
        start_date=body.start_date,
        end_date=body.end_date,
        site_url=body.site_url,
        include_comparison=body.include_comparison,
    )
    await db.commit()
    return SearchConsoleSyncOut.model_validate(sync)


@router.get("/sync/{sync_id}", response_model=SearchConsoleSyncOut)
async def get_gsc_sync(
    sync_id: UUID,
    auth: AuthContext = Depends(get_current_auth),
    db: AsyncSession = Depends(get_db),
) -> SearchConsoleSyncOut:
    sync = await SearchConsoleIntelligenceService(db).get_sync(
        organization_id=auth.organization_id, sync_id=sync_id
    )
    return SearchConsoleSyncOut.model_validate(sync)


@router.get("/performance", response_model=list[SearchConsolePerformanceOut])
async def list_gsc_performance(
    sync_id: UUID | None = Query(default=None),
    dimension_type: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    auth: AuthContext = Depends(get_current_auth),
    db: AsyncSession = Depends(get_db),
) -> list[SearchConsolePerformanceOut]:
    rows = await SearchConsoleIntelligenceService(db).list_performance(
        organization_id=auth.organization_id,
        sync_id=sync_id,
        dimension_type=dimension_type,
        limit=limit,
        offset=offset,
    )
    return [SearchConsolePerformanceOut.model_validate(r) for r in rows]


@router.get("/queries", response_model=list[SearchConsolePerformanceOut])
async def list_gsc_queries(
    sync_id: UUID | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    auth: AuthContext = Depends(get_current_auth),
    db: AsyncSession = Depends(get_db),
) -> list[SearchConsolePerformanceOut]:
    rows = await SearchConsoleIntelligenceService(db).list_queries(
        organization_id=auth.organization_id, sync_id=sync_id, limit=limit, offset=offset
    )
    return [SearchConsolePerformanceOut.model_validate(r) for r in rows]


@router.get("/pages", response_model=list[SearchConsolePerformanceOut])
async def list_gsc_pages(
    sync_id: UUID | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    auth: AuthContext = Depends(get_current_auth),
    db: AsyncSession = Depends(get_db),
) -> list[SearchConsolePerformanceOut]:
    rows = await SearchConsoleIntelligenceService(db).list_pages(
        organization_id=auth.organization_id, sync_id=sync_id, limit=limit, offset=offset
    )
    return [SearchConsolePerformanceOut.model_validate(r) for r in rows]


@router.get("/opportunities", response_model=list[SearchConsoleOpportunityOut])
async def list_gsc_opportunities(
    sync_id: UUID | None = Query(default=None),
    priority: str | None = Query(default=None),
    opportunity_type: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    auth: AuthContext = Depends(get_current_auth),
    db: AsyncSession = Depends(get_db),
) -> list[SearchConsoleOpportunityOut]:
    rows = await SearchConsoleIntelligenceService(db).list_opportunities(
        organization_id=auth.organization_id,
        sync_id=sync_id,
        priority=priority,
        opportunity_type=opportunity_type,
        limit=limit,
        offset=offset,
    )
    return [SearchConsoleOpportunityOut.model_validate(r) for r in rows]


@router.get("/summary", response_model=SearchConsoleSummaryOut)
async def gsc_summary(
    client_id: UUID | None = Query(default=None),
    auth: AuthContext = Depends(get_current_auth),
    db: AsyncSession = Depends(get_db),
) -> SearchConsoleSummaryOut:
    summary = await SearchConsoleIntelligenceService(db).summary(
        organization_id=auth.organization_id, client_id=client_id
    )
    return SearchConsoleSummaryOut.model_validate(summary)
