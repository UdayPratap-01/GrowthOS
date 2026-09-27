"""SEO content briefs API (M9.8)."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import AuthContext, get_current_auth
from app.core.permissions import Permission, require_permission
from app.db.session import get_db
from app.schemas.seo_content_brief import SeoContentBriefGenerateRequest, SeoContentBriefOut
from app.security.limits import ai_limit, seo_content_brief_generate_limit
from app.services.seo_content_brief_service import SeoContentBriefService
from app.services.usage_service import Metric
from app.security.quota import requires_quota

router = APIRouter(prefix="/content-briefs", tags=["seo-content-briefs"])


@router.post(
    "/generate",
    dependencies=[Depends(seo_content_brief_generate_limit), Depends(ai_limit), Depends(requires_quota(Metric.AI_REQUEST))],
)
async def generate_seo_content_brief(
    body: SeoContentBriefGenerateRequest,
    auth: AuthContext = Depends(require_permission(Permission.read)),
    db: AsyncSession = Depends(get_db),
) -> dict:
    result = await SeoContentBriefService(db).generate(
        organization_id=auth.organization_id,
        recommendation_id=body.recommendation_id,
        user_id=auth.user_id,
    )
    await db.commit()
    return result


@router.get("", response_model=list[SeoContentBriefOut])
async def list_seo_content_briefs(
    recommendation_id: UUID | None = Query(default=None),
    brief_type: str | None = Query(default=None),
    status: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    auth: AuthContext = Depends(get_current_auth),
    db: AsyncSession = Depends(get_db),
) -> list[SeoContentBriefOut]:
    rows = await SeoContentBriefService(db).list_briefs(
        organization_id=auth.organization_id,
        recommendation_id=recommendation_id,
        brief_type=brief_type,
        status_filter=status,
        limit=limit,
        offset=offset,
    )
    return [SeoContentBriefOut.model_validate(r) for r in rows]


@router.get("/{brief_id}", response_model=SeoContentBriefOut)
async def get_seo_content_brief(
    brief_id: UUID,
    auth: AuthContext = Depends(get_current_auth),
    db: AsyncSession = Depends(get_db),
) -> SeoContentBriefOut:
    row = await SeoContentBriefService(db).get_brief(
        organization_id=auth.organization_id,
        brief_id=brief_id,
    )
    return SeoContentBriefOut.model_validate(row)


@router.post("/{brief_id}/archive", response_model=SeoContentBriefOut)
async def archive_seo_content_brief(
    brief_id: UUID,
    auth: AuthContext = Depends(require_permission(Permission.read)),
    db: AsyncSession = Depends(get_db),
) -> SeoContentBriefOut:
    row = await SeoContentBriefService(db).archive_brief(
        organization_id=auth.organization_id,
        brief_id=brief_id,
    )
    await db.commit()
    return SeoContentBriefOut.model_validate(row)
