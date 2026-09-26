"""SEO audit engine — crawl observations only."""

from __future__ import annotations

import pytest

from app.seo.audit import run_technical_seo_audit


@pytest.mark.asyncio
async def test_seo_audit_invalid_url():
    result = await run_technical_seo_audit("not-a-url")
    assert result.errors
    assert result.http_status is None


@pytest.mark.asyncio
async def test_seo_audit_extracts_title_and_disclaimer(monkeypatch):
    class FakeResp:
        status_code = 200
        url = "https://example.com/"
        text = """
        <html><head>
        <title>Example Site</title>
        <meta name="description" content="A demo page">
        <link rel="canonical" href="https://example.com/">
        </head><body><h1>Hello</h1><img src="x.png"></body></html>
        """

    class FakeClient:
        async def get(self, url, **kwargs):
            return FakeResp()

        async def head(self, url, **kwargs):
            class HeadResp:
                status_code = 200

            return HeadResp()

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

    monkeypatch.setattr("app.seo.audit.httpx.AsyncClient", lambda **kw: FakeClient())
    result = await run_technical_seo_audit("https://example.com")
    assert result.http_status == 200
    assert result.observations["title"] == "Example Site"
    assert result.observations["meta_description"] == "A demo page"
    assert "http_crawl" in result.data_sources
    dumped = result.as_dict()
    assert "disclaimer" in dumped
    assert "index status" in dumped["disclaimer"].lower()
