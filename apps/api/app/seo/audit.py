"""Technical SEO audit — crawl observations only; never claims index status without GSC data."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urljoin, urlparse

import httpx


class _HeadParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.title: str | None = None
        self.meta_description: str | None = None
        self.canonical: str | None = None
        self.robots: str | None = None
        self.h1: list[str] = []
        self.h2: list[str] = []
        self.images_missing_alt = 0
        self.images_total = 0
        self._in_title = False
        self._current_heading: str | None = None
        self._heading_buffer: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr = {k: (v or "") for k, v in attrs}
        if tag == "title":
            self._in_title = True
        elif tag == "meta":
            name = (attr.get("name") or attr.get("property") or "").lower()
            if name == "description":
                self.meta_description = attr.get("content") or self.meta_description
            if name == "robots":
                self.robots = attr.get("content") or self.robots
        elif tag == "link" and (attr.get("rel") or "").lower() == "canonical":
            self.canonical = attr.get("href") or self.canonical
        elif tag in {"h1", "h2"}:
            self._current_heading = tag
            self._heading_buffer = []
        elif tag == "img":
            self.images_total += 1
            if not (attr.get("alt") or "").strip():
                self.images_missing_alt += 1

    def handle_endtag(self, tag: str) -> None:
        if tag == "title":
            self._in_title = False
        elif tag in {"h1", "h2"} and self._current_heading == tag:
            text = " ".join(self._heading_buffer).strip()
            if text:
                (self.h1 if tag == "h1" else self.h2).append(text)
            self._current_heading = None
            self._heading_buffer = []

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.title = (self.title or "") + data
        elif self._current_heading:
            self._heading_buffer.append(data.strip())


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
    result = SeoAuditResult(url=url, http_status=None)
    result.data_sources.append("http_crawl")

    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        result.errors.append("Invalid URL — must be http(s) with a host")
        return result

    try:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
            resp = await client.get(url, headers={"User-Agent": "GrowthOS-SEO-Audit/1.0"})
        result.http_status = resp.status_code
        if resp.status_code >= 400:
            result.errors.append(f"Page returned HTTP {resp.status_code}")
            return result

        parser = _HeadParser()
        parser.feed(resp.text[:500_000])

        sitemap_url = urljoin(url, "/sitemap.xml")
        sitemap_ok = False
        try:
            async with httpx.AsyncClient(timeout=10, follow_redirects=True) as client:
                sm = await client.head(sitemap_url)
                sitemap_ok = sm.status_code < 400
        except Exception:
            sitemap_ok = False

        structured_data_count = len(re.findall(r"application/ld\+json", resp.text, re.I))

        result.observations = {
            "final_url": str(resp.url),
            "title": (parser.title or "").strip() or None,
            "meta_description": parser.meta_description,
            "canonical": parser.canonical,
            "robots_meta": parser.robots,
            "h1_count": len(parser.h1),
            "h1_text": parser.h1[:5],
            "h2_count": len(parser.h2),
            "images_total": parser.images_total,
            "images_missing_alt": parser.images_missing_alt,
            "sitemap_present": sitemap_ok,
            "structured_data_blocks": structured_data_count,
        }

        if not parser.title:
            result.recommendations.append("Add a unique page title.")
        if not parser.meta_description:
            result.recommendations.append("Add a meta description.")
        if not parser.h1:
            result.recommendations.append("Add at least one H1 heading.")
        if parser.images_missing_alt:
            result.recommendations.append(
                f"Add alt text to {parser.images_missing_alt} image(s) missing alt attributes."
            )
        if not sitemap_ok:
            result.recommendations.append("No sitemap.xml detected at site root — verify sitemap coverage.")
        if parser.robots and "noindex" in parser.robots.lower():
            result.recommendations.append(
                "Robots meta contains noindex — page may be excluded from indexing (crawl observation only)."
            )

    except httpx.TimeoutException:
        result.errors.append("Request timed out")
    except httpx.HTTPError as exc:
        result.errors.append(f"Fetch failed: {type(exc).__name__}")

    return result
