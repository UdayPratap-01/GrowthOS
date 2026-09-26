"""HTML parsing for SEO crawl observations."""

from __future__ import annotations

import re
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urljoin, urlparse

from app.seo.normalize import is_same_site, normalize_url


class PageParser(HTMLParser):
    USER_AGENT = "GrowthOS-SEO-Crawler/1.0"

    def __init__(self, *, page_url: str, root_url: str, include_subdomains: bool = False) -> None:
        super().__init__()
        self.page_url = page_url
        self.root_url = root_url
        self.include_subdomains = include_subdomains
        self.title: str | None = None
        self.meta_description: str | None = None
        self.canonical: str | None = None
        self.robots: str | None = None
        self.language: str | None = None
        self.h1: list[str] = []
        self.h2: list[str] = []
        self.hreflang: list[dict[str, str]] = []
        self.images: list[dict[str, Any]] = []
        self.internal_links: list[str] = []
        self.external_links: list[str] = []
        self.structured_data_blocks = 0
        self._in_title = False
        self._current_heading: str | None = None
        self._heading_buffer: list[str] = []
        self._seen_links: set[str] = set()

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr = {k: (v or "") for k, v in attrs}
        if tag == "html" and not self.language:
            self.language = attr.get("lang") or None
        elif tag == "title":
            self._in_title = True
        elif tag == "meta":
            name = (attr.get("name") or attr.get("property") or "").lower()
            if name == "description":
                self.meta_description = attr.get("content") or self.meta_description
            if name == "robots":
                self.robots = attr.get("content") or self.robots
        elif tag == "link":
            rel = (attr.get("rel") or "").lower()
            if rel == "canonical":
                self.canonical = attr.get("href") or self.canonical
            if rel == "alternate" and (attr.get("hreflang") or "").strip():
                self.hreflang.append({"hreflang": attr.get("hreflang") or "", "href": attr.get("href") or ""})
        elif tag in {"h1", "h2"}:
            self._current_heading = tag
            self._heading_buffer = []
        elif tag == "img":
            self.images.append({"src": attr.get("src"), "alt": attr.get("alt") or ""})
        elif tag == "a" and attr.get("href"):
            self._register_link(attr.get("href") or "")
        elif tag == "script" and (attr.get("type") or "").lower() == "application/ld+json":
            self.structured_data_blocks += 1

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

    def _register_link(self, href: str) -> None:
        normalized = normalize_url(href, base_url=self.page_url)
        if not normalized or normalized in self._seen_links:
            return
        self._seen_links.add(normalized)
        if is_same_site(normalized, self.root_url, include_subdomains=self.include_subdomains):
            self.internal_links.append(normalized)
        else:
            self.external_links.append(normalized)

    def observations(self) -> dict[str, Any]:
        missing_alt = sum(1 for img in self.images if not (img.get("alt") or "").strip())
        return {
            "title": (self.title or "").strip() or None,
            "meta_description": self.meta_description,
            "canonical": self.canonical,
            "robots_meta": self.robots,
            "language": self.language,
            "h1_count": len(self.h1),
            "h1_text": self.h1[:10],
            "h2_count": len(self.h2),
            "h2_text": self.h2[:10],
            "hreflang": self.hreflang[:20],
            "images_total": len(self.images),
            "images_missing_alt": missing_alt,
            "internal_links": self.internal_links[:100],
            "external_links": self.external_links[:50],
            "structured_data_blocks": self.structured_data_blocks,
        }


def count_structured_data_blocks(text: str) -> int:
    return len(re.findall(r"application/ld\+json", text, re.I))
