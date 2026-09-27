"""SEO AI recommendations API (M9.7)."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import AuthContext, get_current_auth
from app.core.permissions import Permission, require_permission
from app.db.session import get_db
from app.schemas.seo_recommendation import (
    SeoRecommendationGenerateRequest,
    SeoRecommendationOut,
    SeoRecommendationSummaryOut,
)
from app.security.limits import ai_limit, seo_recommendation_generate_limit
from app.services.seo_recommendation_service import SeoRecommendationService
from app.services.usage_service import Metric
from app.security.quota import requires_quota

router = APIRouter(prefix="/recommendations", tags=["seo-recommendations"])


@router.post(
    "/generate",
    dependencies=[Depends(seo_recommendation_generate_limit), Depends(ai_limit), Depends(requires_quota(Metric.AI_REQUEST))],
)
async def generate_seo_recommendations(
    body: SeoRecommendationGenerateRequest,
    auth: AuthContext = Depends(require_permission(Permission.read)),
    db: AsyncSession = Depends(get_db),
) -> dict:
    result = await SeoRecommendationService(db).generate(
        organization_id=auth.organization_id,
        user_id=auth.user_id,
        sync_id=body.sync_id,
    )
    await db.commit()
    return result


@router.get("", response_model=list[SeoRecommendationOut])
async def list_seo_recommendations(
    sync_id: UUID | None = Query(default=None),
    type: str | None = Query(default=None, alias="type"),
    priority: str | None = Query(default=None),
    status: str | None = Query(default=None),
    sort: str = Query(default="confidence"),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    auth: AuthContext = Depends(get_current_auth),
    db: AsyncSession = Depends(get_db),
) -> list[SeoRecommendationOut]:
    rows = await SeoRecommendationService(db).list_recommendations(
        organization_id=auth.organization_id,
        sync_id=sync_id,
        recommendation_type=type,
        priority=priority,
        status_filter=status,
        sort=sort,
        limit=limit,
        offset=offset,
    )
    return [SeoRecommendationOut.model_validate(r) for r in rows]


@router.get("/summary", response_model=SeoRecommendationSummaryOut)
async def seo_recommendation_summary(
    sync_id: UUID | None = Query(default=None),
    auth: AuthContext = Depends(get_current_auth),
    db: AsyncSession = Depends(get_db),
) -> SeoRecommendationSummaryOut:
    summary = await SeoRecommendationService(db).summary(organization_id=auth.organization_id, sync_id=sync_id)
    return SeoRecommendationSummaryOut.model_validate(summary)


@router.get("/{recommendation_id}", response_model=SeoRecommendationOut)
async def get_seo_recommendation(
    recommendation_id: UUID,
    auth: AuthContext = Depends(get_current_auth),
    db: AsyncSession = Depends(get_db),
) -> SeoRecommendationOut:
    row = await SeoRecommendationService(db).get_recommendation(
        organization_id=auth.organization_id,
        recommendation_id=recommendation_id,
    )
    return SeoRecommendationOut.model_validate(row)
