"""SEO generated content API (M9.9)."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import AuthContext, get_current_auth
from app.core.permissions import Permission, require_permission
from app.db.session import get_db
from app.schemas.seo_generated_content import (
    SeoContentGenerateRequest,
    SeoGeneratedContentOut,
    SeoGeneratedContentSourceOut,
)
from app.security.limits import ai_limit, seo_content_generate_limit
from app.services.seo_generated_content_service import SeoGeneratedContentService
from app.services.usage_service import Metric
from app.security.quota import requires_quota

router = APIRouter(prefix="/content", tags=["seo-content"])


@router.post(
    "/generate",
    dependencies=[Depends(seo_content_generate_limit), Depends(ai_limit), Depends(requires_quota(Metric.AI_REQUEST))],
)
async def generate_seo_content(
    body: SeoContentGenerateRequest,
    auth: AuthContext = Depends(require_permission(Permission.read)),
    db: AsyncSession = Depends(get_db),
) -> dict:
    result = await SeoGeneratedContentService(db).generate(
        organization_id=auth.organization_id,
        content_brief_id=body.content_brief_id,
        user_id=auth.user_id,
    )
    await db.commit()
    return result


@router.get("", response_model=list[SeoGeneratedContentOut])
async def list_seo_content(
    content_brief_id: UUID | None = Query(default=None),
    content_type: str | None = Query(default=None),
    status: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    auth: AuthContext = Depends(get_current_auth),
    db: AsyncSession = Depends(get_db),
) -> list[SeoGeneratedContentOut]:
    rows = await SeoGeneratedContentService(db).list_content(
        organization_id=auth.organization_id,
        content_brief_id=content_brief_id,
        content_type=content_type,
        status_filter=status,
        limit=limit,
        offset=offset,
    )
    return [SeoGeneratedContentOut.model_validate(r) for r in rows]


@router.get("/{content_id}", response_model=SeoGeneratedContentOut)
async def get_seo_content(
    content_id: UUID,
    auth: AuthContext = Depends(get_current_auth),
    db: AsyncSession = Depends(get_db),
) -> SeoGeneratedContentOut:
    row = await SeoGeneratedContentService(db).get_content(
        organization_id=auth.organization_id,
        content_id=content_id,
    )
    return SeoGeneratedContentOut.model_validate(row)


@router.get("/{content_id}/source", response_model=SeoGeneratedContentSourceOut)
async def get_seo_content_source(
    content_id: UUID,
    auth: AuthContext = Depends(get_current_auth),
    db: AsyncSession = Depends(get_db),
) -> SeoGeneratedContentSourceOut:
    return await SeoGeneratedContentService(db).get_source(
        organization_id=auth.organization_id,
        content_id=content_id,
    )


@router.post("/{content_id}/archive", response_model=SeoGeneratedContentOut)
async def archive_seo_content(
    content_id: UUID,
    auth: AuthContext = Depends(require_permission(Permission.read)),
    db: AsyncSession = Depends(get_db),
) -> SeoGeneratedContentOut:
    row = await SeoGeneratedContentService(db).archive_content(
        organization_id=auth.organization_id,
        content_id=content_id,
    )
    await db.commit()
    return SeoGeneratedContentOut.model_validate(row)
