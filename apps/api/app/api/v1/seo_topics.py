"""Topic clustering API (M9.5)."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import AuthContext, get_current_auth
from app.core.permissions import Permission, require_permission
from app.db.session import get_db
from app.schemas.topic_cluster import (
    TopicAnalyzeRequest,
    TopicClusterOut,
    TopicClusterPageOut,
    TopicClusterQueryOut,
    TopicSummaryOut,
)
from app.security.limits import topic_analyze_limit
from app.services.topic_clustering_service import TopicClusteringService

router = APIRouter(prefix="/topics", tags=["seo-topics"])


@router.post("/analyze", dependencies=[Depends(topic_analyze_limit)])
async def analyze_topics(
    body: TopicAnalyzeRequest,
    auth: AuthContext = Depends(require_permission(Permission.read)),
    db: AsyncSession = Depends(get_db),
) -> dict:
    result = await TopicClusteringService(db).analyze(
        organization_id=auth.organization_id,
        sync_id=body.sync_id,
    )
    await db.commit()
    return result


@router.get("", response_model=list[TopicClusterOut])
async def list_topics(
    sync_id: UUID | None = Query(default=None),
    is_singleton: bool | None = Query(default=None),
    multi_page_signal: bool | None = Query(default=None),
    min_impressions: float | None = Query(default=None, ge=0),
    sort: str = Query(default="impressions"),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    auth: AuthContext = Depends(get_current_auth),
    db: AsyncSession = Depends(get_db),
) -> list[TopicClusterOut]:
    rows = await TopicClusteringService(db).list_topics(
        organization_id=auth.organization_id,
        sync_id=sync_id,
        is_singleton=is_singleton,
        multi_page_signal=multi_page_signal,
        min_impressions=min_impressions,
        sort=sort,
        limit=limit,
        offset=offset,
    )
    return [TopicClusterOut.model_validate(r) for r in rows]


@router.get("/summary", response_model=TopicSummaryOut)
async def topic_summary(
    sync_id: UUID | None = Query(default=None),
    auth: AuthContext = Depends(get_current_auth),
    db: AsyncSession = Depends(get_db),
) -> TopicSummaryOut:
    summary = await TopicClusteringService(db).summary(organization_id=auth.organization_id, sync_id=sync_id)
    return TopicSummaryOut.model_validate(summary)


@router.get("/{topic_id}", response_model=TopicClusterOut)
async def get_topic(
    topic_id: UUID,
    auth: AuthContext = Depends(get_current_auth),
    db: AsyncSession = Depends(get_db),
) -> TopicClusterOut:
    row = await TopicClusteringService(db).get_topic(organization_id=auth.organization_id, topic_id=topic_id)
    return TopicClusterOut.model_validate(row)


@router.get("/{topic_id}/queries", response_model=list[TopicClusterQueryOut])
async def topic_queries(
    topic_id: UUID,
    auth: AuthContext = Depends(get_current_auth),
    db: AsyncSession = Depends(get_db),
) -> list[TopicClusterQueryOut]:
    rows = await TopicClusteringService(db).list_queries(organization_id=auth.organization_id, topic_id=topic_id)
    return [TopicClusterQueryOut.model_validate(r) for r in rows]


@router.get("/{topic_id}/pages", response_model=list[TopicClusterPageOut])
async def topic_pages(
    topic_id: UUID,
    auth: AuthContext = Depends(get_current_auth),
    db: AsyncSession = Depends(get_db),
) -> list[TopicClusterPageOut]:
    rows = await TopicClusteringService(db).list_pages(organization_id=auth.organization_id, topic_id=topic_id)
    return [TopicClusterPageOut.model_validate(r) for r in rows]
