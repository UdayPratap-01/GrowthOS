"""SEO audit engine — crawl observations only."""

from __future__ import annotations

import pytest

from app.seo.audit import run_technical_seo_audit
from app.seo.fetcher import FetchResult


@pytest.mark.asyncio
async def test_seo_audit_invalid_url():
    result = await run_technical_seo_audit("not-a-url")
    assert result.errors
    assert result.http_status is None


@pytest.mark.asyncio
async def test_seo_audit_extracts_title_and_disclaimer(monkeypatch):
    html = """
    <html><head>
    <title>Example Site</title>
    <meta name="description" content="A demo page">
    <link rel="canonical" href="https://example.com/">
    </head><body><h1>Hello</h1><img src="x.png"></body></html>
    """

    async def fake_validate(url: str) -> None:
        return None

    async def fake_fetch(url: str) -> FetchResult:
        if url.endswith("sitemap.xml"):
            return FetchResult(
                requested_url=url,
                final_url=url,
                status_code=200,
                content_type="application/xml",
                body=b"<urlset></urlset>",
                response_bytes=13,
                redirect_count=0,
                response_time_ms=1.0,
            )
        return FetchResult(
            requested_url=url,
            final_url=url,
            status_code=200,
            content_type="text/html",
            body=html.encode(),
            response_bytes=len(html),
            redirect_count=0,
            response_time_ms=2.0,
        )

    class FakeFetcher:
        fetch = staticmethod(fake_fetch)
        head = staticmethod(fake_fetch)

    monkeypatch.setattr("app.seo.audit.validate_url_target", fake_validate)
    monkeypatch.setattr("app.seo.audit.SafeFetcher", lambda **kw: FakeFetcher())
    result = await run_technical_seo_audit("https://example.com")
    assert result.http_status == 200
    assert result.observations["title"] == "Example Site"
    assert result.observations["meta_description"] == "A demo page"
    assert "http_crawl" in result.data_sources
    dumped = result.as_dict()
    assert "disclaimer" in dumped
    assert "index status" in dumped["disclaimer"].lower()
