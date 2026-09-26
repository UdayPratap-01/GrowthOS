"""SEO competitor management and crawling API (M9.6)."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import AuthContext, get_current_auth
from app.core.permissions import Permission, require_permission
from app.db.session import get_db
from app.schemas.seo_competitor import (
    SeoCompetitorCreate,
    SeoCompetitorCrawlOut,
    SeoCompetitorCrawlRequest,
    SeoCompetitorOut,
    SeoCompetitorPageOut,
)
from app.security.limits import seo_competitor_crawl_limit
from app.services.seo_competitor_service import SeoCompetitorService

router = APIRouter(prefix="/competitors", tags=["seo-competitors"])


@router.get("", response_model=list[SeoCompetitorOut])
async def list_competitors(
    auth: AuthContext = Depends(get_current_auth),
    db: AsyncSession = Depends(get_db),
) -> list[SeoCompetitorOut]:
    rows = await SeoCompetitorService(db).list_competitors(organization_id=auth.organization_id)
    return [SeoCompetitorOut.model_validate(r) for r in rows]


@router.post("", response_model=SeoCompetitorOut, status_code=status.HTTP_201_CREATED)
async def create_competitor(
    body: SeoCompetitorCreate,
    auth: AuthContext = Depends(require_permission(Permission.read)),
    db: AsyncSession = Depends(get_db),
) -> SeoCompetitorOut:
    row = await SeoCompetitorService(db).create_competitor(
        organization_id=auth.organization_id,
        root_url=str(body.root_url),
        display_name=body.display_name,
    )
    await db.commit()
    return SeoCompetitorOut.model_validate(row)


@router.get("/{competitor_id}", response_model=SeoCompetitorOut)
async def get_competitor(
    competitor_id: UUID,
    auth: AuthContext = Depends(get_current_auth),
    db: AsyncSession = Depends(get_db),
) -> SeoCompetitorOut:
    row = await SeoCompetitorService(db).get_competitor(
        organization_id=auth.organization_id,
        competitor_id=competitor_id,
    )
    return SeoCompetitorOut.model_validate(row)


@router.delete("/{competitor_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_competitor(
    competitor_id: UUID,
    auth: AuthContext = Depends(require_permission(Permission.read)),
    db: AsyncSession = Depends(get_db),
) -> Response:
    await SeoCompetitorService(db).delete_competitor(
        organization_id=auth.organization_id,
        competitor_id=competitor_id,
    )
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/{competitor_id}/crawl", response_model=SeoCompetitorCrawlOut, status_code=status.HTTP_202_ACCEPTED, dependencies=[Depends(seo_competitor_crawl_limit)])
async def start_competitor_crawl(
    competitor_id: UUID,
    body: SeoCompetitorCrawlRequest,
    auth: AuthContext = Depends(require_permission(Permission.read)),
    db: AsyncSession = Depends(get_db),
) -> SeoCompetitorCrawlOut:
    crawl = await SeoCompetitorService(db).start_crawl(
        organization_id=auth.organization_id,
        competitor_id=competitor_id,
        config={"max_pages": body.max_pages} if body.max_pages else None,
    )
    await db.commit()
    return SeoCompetitorCrawlOut.model_validate(crawl)


@router.get("/{competitor_id}/crawl/{crawl_id}", response_model=SeoCompetitorCrawlOut)
async def get_competitor_crawl(
    competitor_id: UUID,
    crawl_id: UUID,
    auth: AuthContext = Depends(get_current_auth),
    db: AsyncSession = Depends(get_db),
) -> SeoCompetitorCrawlOut:
    row = await SeoCompetitorService(db).get_crawl(
        organization_id=auth.organization_id,
        competitor_id=competitor_id,
        crawl_id=crawl_id,
    )
    return SeoCompetitorCrawlOut.model_validate(row)


@router.get("/{competitor_id}/crawl/{crawl_id}/pages", response_model=list[SeoCompetitorPageOut])
async def list_competitor_crawl_pages(
    competitor_id: UUID,
    crawl_id: UUID,
    auth: AuthContext = Depends(get_current_auth),
    db: AsyncSession = Depends(get_db),
) -> list[SeoCompetitorPageOut]:
    rows = await SeoCompetitorService(db).list_pages(
        organization_id=auth.organization_id,
        competitor_id=competitor_id,
        crawl_id=crawl_id,
    )
    return [SeoCompetitorPageOut.model_validate(r) for r in rows]
