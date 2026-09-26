"""SEO crawl orchestration — tenant-scoped persistence and job enqueue."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.jobs.queue import JobQueue
from app.jobs.registry import SEO_CRAWL
from app.models.enums import SeoCrawlStatus
from app.models.seo import SeoCrawl, SeoCrawlPage
from app.seo.limits import clamp_crawl_limits
from app.seo.normalize import normalize_url
from app.seo.ssrf import SsrfError, validate_url_target


class SeoCrawlService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def create_crawl(
        self,
        *,
        organization_id: UUID,
        user_id: UUID,
        root_url: str,
        client_id: UUID | None = None,
        config: dict | None = None,
    ) -> SeoCrawl:
        normalized = normalize_url(root_url)
        if not normalized:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid root URL")
        try:
            await validate_url_target(normalized)
        except SsrfError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=exc.code) from exc

        limits = clamp_crawl_limits(config)
        active = await self.db.scalar(
            select(func.count())
            .select_from(SeoCrawl)
            .where(
                SeoCrawl.organization_id == organization_id,
                SeoCrawl.status.in_([SeoCrawlStatus.queued, SeoCrawlStatus.running]),
            )
        )
        if (active or 0) >= 3:
            raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail="Too many active crawls")

        crawl = SeoCrawl(
            organization_id=organization_id,
            client_id=client_id,
            root_url=normalized,
            status=SeoCrawlStatus.queued,
            config={
                "max_pages": limits.max_pages,
                "max_depth": limits.max_depth,
                "request_timeout": limits.request_timeout,
                "max_response_bytes": limits.max_response_bytes,
                "request_delay_seconds": limits.request_delay_seconds,
                "include_subdomains": limits.include_subdomains,
                "requested_by": str(user_id),
            },
            stats={"pages_discovered": 0, "pages_crawled": 0, "data_source": "http_crawl"},
        )
        self.db.add(crawl)
        await self.db.flush()

        job = await JobQueue(self.db).enqueue(
            job_type=SEO_CRAWL,
            payload={"crawl_id": str(crawl.id)},
            organization_id=organization_id,
            dedupe_key=f"seo_crawl:{crawl.id}",
            max_attempts=1,
        )
        crawl.job_id = job.id
        await self.db.flush()
        return crawl

    async def get_crawl(self, *, organization_id: UUID, crawl_id: UUID) -> SeoCrawl:
        row = await self._load(organization_id, crawl_id)
        return row

    async def list_pages(self, *, organization_id: UUID, crawl_id: UUID, limit: int = 100, offset: int = 0) -> list[SeoCrawlPage]:
        await self._load(organization_id, crawl_id)
        rows = (
            await self.db.execute(
                select(SeoCrawlPage)
                .where(SeoCrawlPage.crawl_id == crawl_id, SeoCrawlPage.organization_id == organization_id)
                .order_by(SeoCrawlPage.depth, SeoCrawlPage.created_at)
                .offset(offset)
                .limit(min(limit, 500))
            )
        ).scalars().all()
        return list(rows)

    async def cancel_crawl(self, *, organization_id: UUID, crawl_id: UUID) -> SeoCrawl:
        crawl = await self._load(organization_id, crawl_id)
        if crawl.status in {SeoCrawlStatus.completed, SeoCrawlStatus.failed, SeoCrawlStatus.cancelled}:
            return crawl
        crawl.cancel_requested = True
        if crawl.status == SeoCrawlStatus.queued:
            crawl.status = SeoCrawlStatus.cancelled
            crawl.completed_at = datetime.now(timezone.utc)
        if crawl.job_id:
            try:
                await JobQueue(self.db).cancel(job_id=crawl.job_id, organization_id=organization_id)
            except Exception:
                pass
        await self.db.flush()
        return crawl

    async def _load(self, organization_id: UUID, crawl_id: UUID) -> SeoCrawl:
        row = await self.db.scalar(
            select(SeoCrawl).where(SeoCrawl.id == crawl_id, SeoCrawl.organization_id == organization_id).limit(1)
        )
        if not row:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Crawl not found")
        return row
