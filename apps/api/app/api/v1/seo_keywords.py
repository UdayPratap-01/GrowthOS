"""Keyword opportunity API (M9.4)."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import AuthContext, get_current_auth
from app.core.permissions import Permission, require_permission
from app.db.session import get_db
from app.schemas.keyword_opportunity import KeywordAnalyzeRequest, KeywordOpportunityOut, KeywordSummaryOut
from app.security.limits import keyword_analyze_limit
from app.services.keyword_opportunity_service import KeywordOpportunityService

router = APIRouter(prefix="/keywords", tags=["seo-keywords"])


@router.post("/analyze", dependencies=[Depends(keyword_analyze_limit)])
async def analyze_keywords(
    body: KeywordAnalyzeRequest,
    auth: AuthContext = Depends(require_permission(Permission.read)),
    db: AsyncSession = Depends(get_db),
) -> dict:
    result = await KeywordOpportunityService(db).analyze(
        organization_id=auth.organization_id,
        sync_id=body.sync_id,
        brand_terms=body.brand_terms,
    )
    await db.commit()
    return result


@router.get("", response_model=list[KeywordOpportunityOut])
async def list_keywords(
    sync_id: UUID | None = Query(default=None),
    opportunity_type: str | None = Query(default=None),
    priority: str | None = Query(default=None),
    status: str | None = Query(default=None),
    query: str | None = Query(default=None),
    page_url: str | None = Query(default=None),
    min_impressions: float | None = Query(default=None, ge=0),
    sort: str = Query(default="impressions"),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    auth: AuthContext = Depends(get_current_auth),
    db: AsyncSession = Depends(get_db),
) -> list[KeywordOpportunityOut]:
    rows = await KeywordOpportunityService(db).list_opportunities(
        organization_id=auth.organization_id,
        sync_id=sync_id,
        opportunity_type=opportunity_type,
        priority=priority,
        status_filter=status,
        query=query,
        page_url=page_url,
        min_impressions=min_impressions,
        sort=sort,
        limit=limit,
        offset=offset,
    )
    return [KeywordOpportunityOut.model_validate(r) for r in rows]


@router.get("/opportunities", response_model=list[KeywordOpportunityOut])
async def list_keyword_opportunities(
    sync_id: UUID | None = Query(default=None),
    opportunity_type: str | None = Query(default=None),
    priority: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    auth: AuthContext = Depends(get_current_auth),
    db: AsyncSession = Depends(get_db),
) -> list[KeywordOpportunityOut]:
    rows = await KeywordOpportunityService(db).list_opportunities(
        organization_id=auth.organization_id,
        sync_id=sync_id,
        opportunity_type=opportunity_type,
        priority=priority,
        limit=limit,
        offset=offset,
    )
    return [KeywordOpportunityOut.model_validate(r) for r in rows]


@router.get("/summary", response_model=KeywordSummaryOut)
async def keyword_summary(
    sync_id: UUID | None = Query(default=None),
    auth: AuthContext = Depends(get_current_auth),
    db: AsyncSession = Depends(get_db),
) -> KeywordSummaryOut:
    summary = await KeywordOpportunityService(db).summary(organization_id=auth.organization_id, sync_id=sync_id)
    return KeywordSummaryOut.model_validate(summary)


@router.get("/query/{query}", response_model=list[KeywordOpportunityOut])
async def keywords_by_query(
    query: str,
    sync_id: UUID | None = Query(default=None),
    auth: AuthContext = Depends(get_current_auth),
    db: AsyncSession = Depends(get_db),
) -> list[KeywordOpportunityOut]:
    rows = await KeywordOpportunityService(db).get_by_query(
        organization_id=auth.organization_id, query=query, sync_id=sync_id
    )
    return [KeywordOpportunityOut.model_validate(r) for r in rows]


@router.get("/pages/{page_url:path}", response_model=list[KeywordOpportunityOut])
async def keywords_by_page(
    page_url: str,
    sync_id: UUID | None = Query(default=None),
    auth: AuthContext = Depends(get_current_auth),
    db: AsyncSession = Depends(get_db),
) -> list[KeywordOpportunityOut]:
    rows = await KeywordOpportunityService(db).get_by_page(
        organization_id=auth.organization_id, page_url=page_url, sync_id=sync_id
    )
    return [KeywordOpportunityOut.model_validate(r) for r in rows]


@router.get("/{opportunity_id}", response_model=KeywordOpportunityOut)
async def get_keyword_opportunity(
    opportunity_id: UUID,
    auth: AuthContext = Depends(get_current_auth),
    db: AsyncSession = Depends(get_db),
) -> KeywordOpportunityOut:
    row = await KeywordOpportunityService(db).get_opportunity(
        organization_id=auth.organization_id, opportunity_id=opportunity_id
    )
    return KeywordOpportunityOut.model_validate(row)
