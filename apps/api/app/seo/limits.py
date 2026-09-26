"""Server-enforced crawl limits — user config is clamped to safe maximums."""

from __future__ import annotations

from dataclasses import dataclass

from app.core.config import get_settings


@dataclass(frozen=True)
class CrawlLimits:
    max_pages: int = 50
    max_depth: int = 3
    request_timeout: float = 15.0
    max_response_bytes: int = 1_048_576
    max_duration_seconds: float = 600.0
    max_redirects: int = 5
    max_url_length: int = 2048
    max_queue_size: int = 200
    request_delay_seconds: float = 0.25
    include_subdomains: bool = False


def clamp_crawl_limits(raw: dict | None) -> CrawlLimits:
    settings = get_settings()
    raw = raw or {}
    max_pages = min(int(raw.get("max_pages") or settings.seo_crawl_max_pages_default), settings.seo_crawl_max_pages_hard)
    max_depth = min(int(raw.get("max_depth") or settings.seo_crawl_max_depth_default), settings.seo_crawl_max_depth_hard)
    timeout = min(float(raw.get("request_timeout") or settings.seo_crawl_request_timeout_default), settings.seo_crawl_request_timeout_hard)
    max_bytes = min(
        int(raw.get("max_response_bytes") or settings.seo_crawl_max_response_bytes_default),
        settings.seo_crawl_max_response_bytes_hard,
    )
    delay = min(float(raw.get("request_delay_seconds") or settings.seo_crawl_request_delay_default), settings.seo_crawl_request_delay_hard)
    return CrawlLimits(
        max_pages=max(1, max_pages),
        max_depth=max(0, max_depth),
        request_timeout=max(1.0, timeout),
        max_response_bytes=max(16_384, max_bytes),
        max_duration_seconds=float(settings.seo_crawl_max_duration_seconds),
        max_redirects=int(settings.seo_crawl_max_redirects),
        max_url_length=int(settings.seo_crawl_max_url_length),
        max_queue_size=min(max_pages * 4, settings.seo_crawl_max_queue_size),
        request_delay_seconds=max(0.0, delay),
        include_subdomains=bool(raw.get("include_subdomains")),
    )


def clamp_competitor_crawl_limits(raw: dict | None) -> CrawlLimits:
    settings = get_settings()
    raw = raw or {}
    max_pages = min(
        int(raw.get("max_pages") or settings.seo_competitor_crawl_max_pages_default),
        settings.seo_competitor_crawl_max_pages_hard,
    )
    max_depth = min(int(raw.get("max_depth") or 2), settings.seo_crawl_max_depth_hard)
    timeout = min(float(raw.get("request_timeout") or settings.seo_crawl_request_timeout_default), settings.seo_crawl_request_timeout_hard)
    max_bytes = min(
        int(raw.get("max_response_bytes") or settings.seo_crawl_max_response_bytes_default),
        settings.seo_crawl_max_response_bytes_hard,
    )
    delay = min(float(raw.get("request_delay_seconds") or settings.seo_crawl_request_delay_default), settings.seo_crawl_request_delay_hard)
    return CrawlLimits(
        max_pages=max(1, max_pages),
        max_depth=max(0, max_depth),
        request_timeout=max(1.0, timeout),
        max_response_bytes=max(16_384, max_bytes),
        max_duration_seconds=min(float(settings.seo_crawl_max_duration_seconds), 300.0),
        max_redirects=int(settings.seo_crawl_max_redirects),
        max_url_length=int(settings.seo_crawl_max_url_length),
        max_queue_size=min(max_pages * 4, 500),
        request_delay_seconds=max(0.0, delay),
        include_subdomains=False,
    )
