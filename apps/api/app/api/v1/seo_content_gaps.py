"""Content-gap analysis API (M9.6)."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import AuthContext, get_current_auth
from app.core.permissions import Permission, require_permission
from app.db.session import get_db
from app.schemas.content_gap import ContentGapAnalyzeRequest, ContentGapOut, ContentGapSummaryOut
from app.security.limits import content_gap_analyze_limit
from app.services.content_gap_service import ContentGapService

router = APIRouter(prefix="/content-gaps", tags=["seo-content-gaps"])


@router.post("/analyze", dependencies=[Depends(content_gap_analyze_limit)])
async def analyze_content_gaps(
    body: ContentGapAnalyzeRequest,
    auth: AuthContext = Depends(require_permission(Permission.read)),
    db: AsyncSession = Depends(get_db),
) -> dict:
    result = await ContentGapService(db).analyze(
        organization_id=auth.organization_id,
        sync_id=body.sync_id,
    )
    await db.commit()
    return result


@router.get("", response_model=list[ContentGapOut])
async def list_content_gaps(
    sync_id: UUID | None = Query(default=None),
    competitor_id: UUID | None = Query(default=None),
    gap_type: str | None = Query(default=None),
    status: str | None = Query(default=None),
    min_similarity: float | None = Query(default=None, ge=0, le=1),
    max_similarity: float | None = Query(default=None, ge=0, le=1),
    sort: str = Query(default="similarity"),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    auth: AuthContext = Depends(get_current_auth),
    db: AsyncSession = Depends(get_db),
) -> list[ContentGapOut]:
    rows = await ContentGapService(db).list_gaps(
        organization_id=auth.organization_id,
        sync_id=sync_id,
        competitor_id=competitor_id,
        gap_type=gap_type,
        status_filter=status,
        min_similarity=min_similarity,
        max_similarity=max_similarity,
        sort=sort,
        limit=limit,
        offset=offset,
    )
    return [ContentGapOut.model_validate(r) for r in rows]


@router.get("/summary", response_model=ContentGapSummaryOut)
async def content_gap_summary(
    sync_id: UUID | None = Query(default=None),
    auth: AuthContext = Depends(get_current_auth),
    db: AsyncSession = Depends(get_db),
) -> ContentGapSummaryOut:
    summary = await ContentGapService(db).summary(organization_id=auth.organization_id, sync_id=sync_id)
    return ContentGapSummaryOut.model_validate(summary)


@router.get("/{gap_id}", response_model=ContentGapOut)
async def get_content_gap(
    gap_id: UUID,
    auth: AuthContext = Depends(get_current_auth),
    db: AsyncSession = Depends(get_db),
) -> ContentGapOut:
    row = await ContentGapService(db).get_gap(organization_id=auth.organization_id, gap_id=gap_id)
    return ContentGapOut.model_validate(row)
