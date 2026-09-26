"""Full-site read-only SEO crawler."""

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
from app.models.seo import SeoCrawl, SeoCrawlPage
from app.seo.fetcher import SafeFetcher
from app.seo.limits import CrawlLimits
from app.seo.normalize import is_same_site, normalize_url
from app.seo.parser import PageParser
from app.seo.robots import RobotsRules, parse_robots_txt, path_allowed, robots_url_for
from app.seo.sitemap import default_sitemap_urls, parse_sitemap_xml
from app.seo.ssrf import SsrfError, validate_url_target


class SiteCrawler:
    def __init__(self, *, limits: CrawlLimits, fetcher: SafeFetcher | None = None) -> None:
        self.limits = limits
        self.fetcher = fetcher or SafeFetcher(
            timeout=limits.request_timeout,
            max_response_bytes=limits.max_response_bytes,
            max_redirects=limits.max_redirects,
        )

    async def run(self, db: AsyncSession, crawl: SeoCrawl) -> dict[str, Any]:
        root = normalize_url(crawl.root_url)
        if not root:
            return await self._fail(db, crawl, "invalid_root_url", "Root URL could not be normalized")

        try:
            await validate_url_target(root)
        except SsrfError as exc:
            return await self._fail(db, crawl, exc.code, exc.message)

        crawl.status = SeoCrawlStatus.running
        crawl.started_at = datetime.now(timezone.utc)
        crawl.stats = {
            "pages_discovered": 0,
            "pages_crawled": 0,
            "pages_failed": 0,
            "pages_blocked_robots": 0,
            "data_source": "http_crawl",
        }
        await db.flush()

        robots_rules = await self._load_robots(root)
        seed_urls = await self._seed_urls(root, robots_rules)
        queue: deque[tuple[str, int, str | None]] = deque()
        seen: set[str] = set()
        for seed in seed_urls:
            normalized = normalize_url(seed, base_url=root)
            if normalized and normalized not in seen:
                seen.add(normalized)
                queue.append((normalized, 0, None))

        started = datetime.now(timezone.utc)
        pages_crawled = 0

        while queue and pages_crawled < self.limits.max_pages:
            if await self._is_cancelled(db, crawl.id):
                return await self._cancel(db, crawl)

            elapsed = (datetime.now(timezone.utc) - started).total_seconds()
            if elapsed > self.limits.max_duration_seconds:
                break
            if len(seen) > self.limits.max_queue_size:
                break

            url, depth, referrer = queue.popleft()
            if depth > self.limits.max_depth:
                continue
            path = urlparse(url).path or "/"
            if not path_allowed(robots_rules, path):
                crawl.stats["pages_blocked_robots"] = int(crawl.stats.get("pages_blocked_robots") or 0) + 1
                await self._persist_page(
                    db,
                    crawl,
                    url=url,
                    depth=depth,
                    referrer=referrer,
                    fetch=None,
                    error_code="blocked_by_robots",
                )
                await db.flush()
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
                parser = PageParser(
                    page_url=fetch.final_url or url,
                    root_url=root,
                    include_subdomains=self.limits.include_subdomains,
                )
                parser.feed(fetch.body[: self.limits.max_response_bytes].decode("utf-8", errors="replace"))
                observations = parser.observations()
                for link in observations.get("internal_links") or []:
                    if link in seen or len(seen) >= self.limits.max_queue_size:
                        continue
                    if not is_same_site(link, root, include_subdomains=self.limits.include_subdomains):
                        continue
                    seen.add(link)
                    queue.append((link, depth + 1, url))
            elif (fetch.content_type or "") in {"text/plain", "application/xml", "text/xml"} and url.endswith("robots.txt"):
                observations = {"robots_txt": True}
            elif fetch.content_type and fetch.content_type.endswith("xml") and "sitemap" in url:
                observations = {"sitemap": True, "bytes": fetch.response_bytes}
            else:
                observations = {"content_type": fetch.content_type, "non_html": True}

            await self._persist_page(
                db,
                crawl,
                url=url,
                depth=depth,
                referrer=referrer,
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
        robots_url = robots_url_for(root_url)
        fetch = await self.fetcher.fetch(robots_url)
        if fetch.error_code or not fetch.body or (fetch.status_code or 500) >= 400:
            return RobotsRules()
        try:
            return parse_robots_txt(fetch.body.decode("utf-8", errors="replace"))
        except Exception:
            return RobotsRules()

    async def _seed_urls(self, root: str, rules: RobotsRules) -> list[str]:
        seeds = [root]
        sitemap_candidates = list(rules.sitemaps) or default_sitemap_urls(root)
        for sm_url in sitemap_candidates[:5]:
            normalized = normalize_url(sm_url, base_url=root)
            if not normalized:
                continue
            fetch = await self.fetcher.fetch(normalized)
            if fetch.error_code or not fetch.body:
                continue
            try:
                page_urls, _nested = parse_sitemap_xml(fetch.body, max_urls=self.limits.max_pages)
            except Exception:
                continue
            for page in page_urls:
                n = normalize_url(page, base_url=root)
                if n and is_same_site(n, root, include_subdomains=self.limits.include_subdomains):
                    seeds.append(n)
        return list(dict.fromkeys(seeds))

    async def _persist_page(
        self,
        db: AsyncSession,
        crawl: SeoCrawl,
        *,
        url: str,
        depth: int,
        referrer: str | None,
        fetch,
        observations: dict | None = None,
        error_code: str | None = None,
    ) -> None:
        existing = await db.scalar(
            select(SeoCrawlPage).where(SeoCrawlPage.crawl_id == crawl.id, SeoCrawlPage.url == url).limit(1)
        )
        if existing:
            return
        page = SeoCrawlPage(
            crawl_id=crawl.id,
            organization_id=crawl.organization_id,
            url=url,
            final_url=getattr(fetch, "final_url", None),
            depth=depth,
            referrer_url=referrer,
            http_status=getattr(fetch, "status_code", None),
            content_type=getattr(fetch, "content_type", None),
            response_bytes=int(getattr(fetch, "response_bytes", 0) or 0),
            redirect_count=int(getattr(fetch, "redirect_count", 0) or 0),
            response_time_ms=getattr(fetch, "response_time_ms", None),
            error_code=error_code,
            observations={
                **(observations or {}),
                "data_provenance": "http_crawl",
                "note": "Crawler observation only — not Search Console index verification.",
            },
            data_source="http_crawl",
        )
        db.add(page)

    async def _is_cancelled(self, db: AsyncSession, crawl_id: UUID) -> bool:
        row = await db.get(SeoCrawl, crawl_id)
        return bool(row and row.cancel_requested)

    async def _fail(self, db: AsyncSession, crawl: SeoCrawl, code: str, message: str) -> dict[str, Any]:
        crawl.status = SeoCrawlStatus.failed
        crawl.error = message[:500]
        crawl.completed_at = datetime.now(timezone.utc)
        crawl.stats = {**(crawl.stats or {}), "error_code": code}
        await db.flush()
        return {"crawl_id": str(crawl.id), "status": crawl.status.value, "error": message, "error_code": code}

    async def _cancel(self, db: AsyncSession, crawl: SeoCrawl) -> dict[str, Any]:
        crawl.status = SeoCrawlStatus.cancelled
        crawl.completed_at = datetime.now(timezone.utc)
        await db.flush()
        return {"crawl_id": str(crawl.id), "status": crawl.status.value, "cancelled": True}
