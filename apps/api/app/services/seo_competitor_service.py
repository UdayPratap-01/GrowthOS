"""SEO competitor management and bounded crawling."""

from __future__ import annotations

from datetime import datetime, timezone
from urllib.parse import urlparse
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.jobs.queue import JobQueue
from app.jobs.registry import SEO_COMPETITOR_CRAWL
from app.models.enums import SeoCrawlStatus
from app.models.seo_competitor import SeoCompetitor, SeoCompetitorCrawl, SeoCompetitorPage
from app.seo.limits import clamp_competitor_crawl_limits
from app.seo.normalize import normalize_url, registrable_domain
from app.seo.ssrf import SsrfError, validate_url_target


class SeoCompetitorService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def create_competitor(
        self,
        *,
        organization_id: UUID,
        root_url: str,
        display_name: str | None = None,
    ) -> SeoCompetitor:
        settings = get_settings()
        count = await self.db.scalar(
            select(func.count()).select_from(SeoCompetitor).where(
                SeoCompetitor.organization_id == organization_id,
                SeoCompetitor.status == "active",
            )
        )
        if (count or 0) >= settings.seo_competitor_max_per_org:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Maximum competitors reached")

        normalized = normalize_url(root_url)
        if not normalized:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid competitor URL")
        parsed = urlparse(normalized)
        if parsed.scheme not in {"http", "https"}:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="invalid_scheme")
        try:
            await validate_url_target(normalized)
        except SsrfError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=exc.code) from exc

        domain = registrable_domain(parsed.hostname or "") or (parsed.hostname or normalized)
        existing = await self.db.scalar(
            select(SeoCompetitor).where(
                SeoCompetitor.organization_id == organization_id,
                SeoCompetitor.root_url == normalized,
            ).limit(1)
        )
        if existing:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Competitor already exists")

        row = SeoCompetitor(
            organization_id=organization_id,
            display_name=(display_name or domain)[:255],
            root_url=normalized,
            domain=domain[:512],
            status="active",
        )
        self.db.add(row)
        await self.db.flush()
        return row

    async def list_competitors(self, *, organization_id: UUID) -> list[SeoCompetitor]:
        rows = (
            await self.db.execute(
                select(SeoCompetitor)
                .where(SeoCompetitor.organization_id == organization_id, SeoCompetitor.status == "active")
                .order_by(SeoCompetitor.created_at.desc())
            )
        ).scalars().all()
        return list(rows)

    async def get_competitor(self, *, organization_id: UUID, competitor_id: UUID) -> SeoCompetitor:
        row = await self.db.scalar(
            select(SeoCompetitor).where(
                SeoCompetitor.id == competitor_id,
                SeoCompetitor.organization_id == organization_id,
            ).limit(1)
        )
        if not row:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Competitor not found")
        return row

    async def delete_competitor(self, *, organization_id: UUID, competitor_id: UUID) -> None:
        row = await self.get_competitor(organization_id=organization_id, competitor_id=competitor_id)
        row.status = "archived"
        await self.db.flush()

    async def start_crawl(
        self,
        *,
        organization_id: UUID,
        competitor_id: UUID,
        config: dict | None = None,
    ) -> SeoCompetitorCrawl:
        competitor = await self.get_competitor(organization_id=organization_id, competitor_id=competitor_id)
        active = await self.db.scalar(
            select(func.count())
            .select_from(SeoCompetitorCrawl)
            .where(
                SeoCompetitorCrawl.organization_id == organization_id,
                SeoCompetitorCrawl.competitor_id == competitor_id,
                SeoCompetitorCrawl.status.in_([SeoCrawlStatus.queued, SeoCrawlStatus.running]),
            )
        )
        if (active or 0) >= 1:
            raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail="Competitor crawl already active")

        limits = clamp_competitor_crawl_limits(config)
        crawl = SeoCompetitorCrawl(
            competitor_id=competitor.id,
            organization_id=organization_id,
            root_url=competitor.root_url,
            status=SeoCrawlStatus.queued,
            config={
                "max_pages": limits.max_pages,
                "max_depth": limits.max_depth,
                "request_timeout": limits.request_timeout,
                "max_response_bytes": limits.max_response_bytes,
                "request_delay_seconds": limits.request_delay_seconds,
            },
            stats={"data_source": "competitor_crawl"},
        )
        self.db.add(crawl)
        await self.db.flush()

        job = await JobQueue(self.db).enqueue(
            job_type=SEO_COMPETITOR_CRAWL,
            payload={"crawl_id": str(crawl.id)},
            organization_id=organization_id,
            dedupe_key=f"seo_competitor_crawl:{crawl.id}",
            max_attempts=1,
        )
        crawl.job_id = job.id
        await self.db.flush()
        return crawl

    async def get_crawl(self, *, organization_id: UUID, competitor_id: UUID, crawl_id: UUID) -> SeoCompetitorCrawl:
        row = await self.db.scalar(
            select(SeoCompetitorCrawl).where(
                SeoCompetitorCrawl.id == crawl_id,
                SeoCompetitorCrawl.competitor_id == competitor_id,
                SeoCompetitorCrawl.organization_id == organization_id,
            ).limit(1)
        )
        if not row:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Competitor crawl not found")
        return row

    async def list_pages(
        self,
        *,
        organization_id: UUID,
        competitor_id: UUID,
        crawl_id: UUID,
        limit: int = 100,
        offset: int = 0,
    ) -> list[SeoCompetitorPage]:
        await self.get_crawl(organization_id=organization_id, competitor_id=competitor_id, crawl_id=crawl_id)
        rows = (
            await self.db.execute(
                select(SeoCompetitorPage)
                .where(
                    SeoCompetitorPage.crawl_id == crawl_id,
                    SeoCompetitorPage.organization_id == organization_id,
                )
                .order_by(SeoCompetitorPage.created_at)
                .offset(offset)
                .limit(min(limit, 500))
            )
        ).scalars().all()
        return list(rows)
