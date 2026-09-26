"""Technical SEO audit — single-page crawl observations."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urljoin

import httpx

from app.seo.fetcher import SafeFetcher
from app.seo.normalize import normalize_url
from app.seo.parser import PageParser, count_structured_data_blocks
from app.seo.ssrf import SsrfError, validate_url_target


@dataclass
class SeoAuditResult:
    url: str
    http_status: int | None
    observations: dict[str, Any] = field(default_factory=dict)
    recommendations: list[str] = field(default_factory=list)
    data_sources: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "url": self.url,
            "http_status": self.http_status,
            "observations": self.observations,
            "recommendations": self.recommendations,
            "data_sources": self.data_sources,
            "errors": self.errors,
            "disclaimer": (
                "Technical crawl observations only. Index status requires Search Console data. "
                "Recommendations are advisory — not guaranteed ranking outcomes."
            ),
        }


async def run_technical_seo_audit(url: str, *, timeout: float = 20.0) -> SeoAuditResult:
    normalized = normalize_url(url)
    result = SeoAuditResult(url=url, http_status=None)
    result.data_sources.append("http_crawl")

    if not normalized:
        result.errors.append("Invalid URL — must be http(s) with a host")
        return result

    try:
        await validate_url_target(normalized)
    except SsrfError as exc:
        result.errors.append(exc.code)
        return result

    fetcher = SafeFetcher(timeout=timeout, max_response_bytes=1_048_576, max_redirects=5)
    fetch = await fetcher.fetch(normalized)
    result.http_status = fetch.status_code
    if fetch.error_code:
        result.errors.append(fetch.error_code)
        return result
    if (fetch.status_code or 500) >= 400:
        result.errors.append(f"Page returned HTTP {fetch.status_code}")
        return result

    text = fetch.body.decode("utf-8", errors="replace")[:500_000]
    parser = PageParser(page_url=fetch.final_url or normalized, root_url=normalized)
    parser.feed(text)
    observations = parser.observations()
    sitemap_url = urljoin(normalized, "/sitemap.xml")
    sitemap_fetch = await fetcher.head(sitemap_url)
    sitemap_ok = (sitemap_fetch.status_code or 500) < 400 and not sitemap_fetch.error_code

    result.observations = {
        "final_url": fetch.final_url,
        **observations,
        "sitemap_present": sitemap_ok,
        "structured_data_blocks": count_structured_data_blocks(text),
    }

    if not observations.get("title"):
        result.recommendations.append("Add a unique page title.")
    if not observations.get("meta_description"):
        result.recommendations.append("Add a meta description.")
    if not observations.get("h1_count"):
        result.recommendations.append("Add at least one H1 heading.")
    if observations.get("images_missing_alt"):
        result.recommendations.append(
            f"Add alt text to {observations['images_missing_alt']} image(s) missing alt attributes."
        )
    if not sitemap_ok:
        result.recommendations.append("No sitemap.xml detected at site root — verify sitemap coverage.")
    if observations.get("robots_meta") and "noindex" in str(observations["robots_meta"]).lower():
        result.recommendations.append(
            "Robots meta contains noindex — page may be excluded from indexing (crawl observation only)."
        )

    return result
