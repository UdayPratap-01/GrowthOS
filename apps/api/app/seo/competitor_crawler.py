"""Bounded competitor site crawler — reuses M9.1 fetcher/parser/SSRF/robots."""

from __future__ import annotations

import asyncio
from collections import deque
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.enums import SeoCrawlStatus
from app.models.seo_competitor import SeoCompetitorCrawl, SeoCompetitorPage
from app.seo.content_gap.representation import extract_page_representation
from app.seo.fetcher import SafeFetcher
from app.seo.limits import CrawlLimits
from app.seo.normalize import is_same_site, normalize_url
from app.seo.parser import PageParser
from app.seo.robots import RobotsRules, parse_robots_txt, path_allowed, robots_url_for
from app.seo.sitemap import default_sitemap_urls, parse_sitemap_xml
from app.seo.ssrf import SsrfError, validate_url_target


class CompetitorSiteCrawler:
    def __init__(self, *, limits: CrawlLimits, fetcher: SafeFetcher | None = None) -> None:
        self.limits = limits
        self.fetcher = fetcher or SafeFetcher(
            timeout=limits.request_timeout,
            max_response_bytes=limits.max_response_bytes,
            max_redirects=limits.max_redirects,
        )

    async def run(self, db: AsyncSession, crawl: SeoCompetitorCrawl) -> dict[str, Any]:
        root = normalize_url(crawl.root_url)
        if not root:
            return await self._fail(db, crawl, "invalid_root_url", "Root URL could not be normalized")
        try:
            await validate_url_target(root)
        except SsrfError as exc:
            return await self._fail(db, crawl, exc.code, exc.message)

        crawl.status = SeoCrawlStatus.running
        crawl.started_at = datetime.now(timezone.utc)
        crawl.stats = {"pages_discovered": 0, "pages_crawled": 0, "pages_failed": 0, "data_source": "competitor_crawl"}
        await db.flush()

        robots_rules = await self._load_robots(root)
        queue: deque[tuple[str, int]] = deque([(root, 0)])
        seen: set[str] = {root}
        pages_crawled = 0
        started = datetime.now(timezone.utc)

        while queue and pages_crawled < self.limits.max_pages:
            if await self._is_cancelled(db, crawl.id):
                return await self._cancel(db, crawl)
            if (datetime.now(timezone.utc) - started).total_seconds() > self.limits.max_duration_seconds:
                break

            url, depth = queue.popleft()
            if depth > self.limits.max_depth:
                continue
            path = urlparse(url).path or "/"
            if not path_allowed(robots_rules, path):
                await self._persist_page(db, crawl, url=url, depth=depth, fetch=None, error_code="blocked_by_robots")
                continue

            if self.limits.request_delay_seconds > 0:
                await asyncio.sleep(self.limits.request_delay_seconds)

            fetch = await self.fetcher.fetch(url)
            pages_crawled += 1
            crawl.stats["pages_crawled"] = pages_crawled
            crawl.stats["pages_discovered"] = len(seen)
            if fetch.error_code:
                crawl.stats["pages_failed"] = int(crawl.stats.get("pages_failed") or 0) + 1

            observations: dict[str, Any] = {}
            if fetch.body and (fetch.content_type or "").startswith("text/html"):
                parser = PageParser(page_url=fetch.final_url or url, root_url=root, include_subdomains=False)
                parser.feed(fetch.body[: self.limits.max_response_bytes].decode("utf-8", errors="replace"))
                observations = parser.observations()
                for link in observations.get("internal_links") or []:
                    if link in seen or len(seen) >= self.limits.max_queue_size:
                        continue
                    if not is_same_site(link, root, include_subdomains=False):
                        continue
                    seen.add(link)
                    queue.append((link, depth + 1))

            await self._persist_page(
                db,
                crawl,
                url=url,
                depth=depth,
                fetch=fetch,
                observations=observations,
                error_code=fetch.error_code,
            )
            await db.flush()

        crawl.status = SeoCrawlStatus.completed
        crawl.completed_at = datetime.now(timezone.utc)
        crawl.stats["pages_discovered"] = len(seen)
        await db.flush()
        return {"crawl_id": str(crawl.id), "status": crawl.status.value, "stats": crawl.stats}

    async def _load_robots(self, root_url: str) -> RobotsRules:
        fetch = await self.fetcher.fetch(robots_url_for(root_url))
        if fetch.error_code or not fetch.body or (fetch.status_code or 500) >= 400:
            return RobotsRules()
        try:
            return parse_robots_txt(fetch.body.decode("utf-8", errors="replace"))
        except Exception:
            return RobotsRules()

    async def _persist_page(
        self,
        db: AsyncSession,
        crawl: SeoCompetitorCrawl,
        *,
        url: str,
        depth: int,
        fetch,
        observations: dict | None = None,
        error_code: str | None = None,
    ) -> None:
        obs = observations or {}
        rep = extract_page_representation(url=url, observations=obs) if obs else None
        page = SeoCompetitorPage(
            crawl_id=crawl.id,
            competitor_id=crawl.competitor_id,
            organization_id=crawl.organization_id,
            url=url,
            final_url=getattr(fetch, "final_url", None) if fetch else None,
            depth=depth,
            http_status=getattr(fetch, "status_code", None) if fetch else None,
            content_type=getattr(fetch, "content_type", None) if fetch else None,
            title=rep.title if rep else None,
            meta_description=obs.get("meta_description"),
            h1=rep.h1 if rep else None,
            headings=rep.headings if rep else [],
            topic_label=rep.topic_label if rep else None,
            token_terms=sorted(list(rep.tokens))[:100] if rep else [],
            word_count=rep.word_count if rep else 0,
            structured_data_present=bool(obs.get("structured_data_blocks")),
            observations={
                **obs,
                "data_provenance": "competitor_crawl",
                "note": "Competitor observation only — not ranking or traffic data.",
            },
            fetched_at=datetime.now(timezone.utc),
            error_code=error_code,
        )
        db.add(page)

    async def _is_cancelled(self, db: AsyncSession, crawl_id: UUID) -> bool:
        row = await db.get(SeoCompetitorCrawl, crawl_id)
        return bool(row and row.cancel_requested)

    async def _fail(self, db: AsyncSession, crawl: SeoCompetitorCrawl, code: str, message: str) -> dict[str, Any]:
        crawl.status = SeoCrawlStatus.failed
        crawl.error = message[:500]
        crawl.completed_at = datetime.now(timezone.utc)
        crawl.stats = {**(crawl.stats or {}), "error_code": code}
        await db.flush()
        return {"crawl_id": str(crawl.id), "status": crawl.status.value, "error": message, "error_code": code}

    async def _cancel(self, db: AsyncSession, crawl: SeoCompetitorCrawl) -> dict[str, Any]:
        crawl.status = SeoCrawlStatus.cancelled
        crawl.completed_at = datetime.now(timezone.utc)
        await db.flush()
        return {"crawl_id": str(crawl.id), "status": crawl.status.value, "cancelled": True}
