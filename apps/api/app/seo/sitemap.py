"""Sitemap discovery and safe XML parsing."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from urllib.parse import urljoin

MAX_SITEMAP_BYTES = 2_000_000
MAX_SITEMAP_URLS = 500
MAX_SITEMAP_DEPTH = 2


def parse_sitemap_xml(content: bytes, *, max_urls: int = MAX_SITEMAP_URLS) -> tuple[list[str], list[str]]:
    """Return (page_urls, nested_sitemap_urls)."""
    if len(content) > MAX_SITEMAP_BYTES:
        raise ValueError("sitemap_too_large")
    root = ET.fromstring(content)
    tag = _local_name(root.tag)
    page_urls: list[str] = []
    nested: list[str] = []
    if tag == "sitemapindex":
        for node in root:
            if _local_name(node.tag) != "sitemap":
                continue
            for child in node:
                if _local_name(child.tag) == "loc" and child.text:
                    nested.append(child.text.strip())
                    break
    elif tag == "urlset":
        for node in root:
            if _local_name(node.tag) != "url":
                continue
            for child in node:
                if _local_name(child.tag) == "loc" and child.text:
                    page_urls.append(child.text.strip())
                    break
            if len(page_urls) >= max_urls:
                break
    return page_urls[:max_urls], nested[:50]


def _local_name(tag: str) -> str:
    if "}" in tag:
        return tag.rsplit("}", 1)[-1].lower()
    return tag.lower()


def default_sitemap_urls(root_url: str) -> list[str]:
    return [urljoin(root_url, "/sitemap.xml")]
