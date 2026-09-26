"""M9.2 — Technical SEO analysis engine tests."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from httpx import ASGITransport, AsyncClient

from app.core.security import hash_password
from app.db.session import AsyncSessionLocal
from app.models.enums import MemberRole, SeoCrawlStatus
from app.models.organization import Organization, OrganizationMember
from app.models.seo import SeoCrawl, SeoCrawlPage
from app.models.user import User
from app.seo.analysis.engine import analyze_crawl, _dedupe_findings
from app.seo.analysis.types import FindingDraft
from app.main import app


def _page(
    *,
    url: str,
    observations: dict | None = None,
    http_status: int = 200,
    depth: int = 0,
    referrer: str | None = None,
    error_code: str | None = None,
    redirect_count: int = 0,
    final_url: str | None = None,
    content_type: str = "text/html",
    response_bytes: int = 1000,
) -> SeoCrawlPage:
    pid = uuid.uuid4()
    cid = uuid.uuid4()
    oid = uuid.uuid4()
    return SeoCrawlPage(
        id=pid,
        crawl_id=cid,
        organization_id=oid,
        url=url,
        final_url=final_url or url,
        depth=depth,
        referrer_url=referrer,
        http_status=http_status,
        content_type=content_type,
        response_bytes=response_bytes,
        redirect_count=redirect_count,
        error_code=error_code,
        observations={
            **(observations or {}),
            "data_provenance": "http_crawl",
        },
        data_source="http_crawl",
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )


def _crawl(root: str = "https://example.com/") -> SeoCrawl:
    return SeoCrawl(
        id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        root_url=root,
        status=SeoCrawlStatus.completed,
        config={"include_subdomains": False},
        stats={},
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )


def test_missing_title():
    crawl = _crawl()
    pages = [_page(url="https://example.com/", observations={"title": None, "h1_count": 1})]
    findings = analyze_crawl(crawl, pages)
    assert any(f.rule_id == "SEO_TITLE_MISSING" for f in findings)


def test_duplicate_title():
    crawl = _crawl()
    pages = [
        _page(url="https://example.com/a", observations={"title": "Same Title", "h1_count": 1}),
        _page(url="https://example.com/b", observations={"title": "Same Title", "h1_count": 1}),
    ]
    findings = analyze_crawl(crawl, pages)
    assert sum(1 for f in findings if f.rule_id == "SEO_TITLE_DUPLICATE") == 2


def test_short_and_long_title():
    crawl = _crawl()
    pages = [
        _page(url="https://example.com/short", observations={"title": "Hi", "h1_count": 1}),
        _page(url="https://example.com/long", observations={"title": "X" * 100, "h1_count": 1}),
    ]
    findings = analyze_crawl(crawl, pages)
    assert any(f.rule_id == "SEO_TITLE_SHORT" for f in findings)
    assert any(f.rule_id == "SEO_TITLE_LONG" for f in findings)


def test_meta_description_rules():
    crawl = _crawl()
    pages = [
        _page(url="https://example.com/", observations={"title": "Ok title here", "h1_count": 1}),
        _page(url="https://example.com/dup", observations={"title": "T", "meta_description": "Shared desc " * 5, "h1_count": 1}),
        _page(url="https://example.com/dup2", observations={"title": "T2", "meta_description": "Shared desc " * 5, "h1_count": 1}),
    ]
    findings = analyze_crawl(crawl, pages)
    assert any(f.rule_id == "SEO_META_MISSING" for f in findings)
    assert any(f.rule_id == "SEO_META_DUPLICATE" for f in findings)


def test_h1_rules():
    crawl = _crawl()
    pages = [_page(url="https://example.com/", observations={"title": "Page", "h1_count": 0, "h2_count": 2})]
    findings = analyze_crawl(crawl, pages)
    assert any(f.rule_id == "SEO_H1_MISSING" for f in findings)
    assert any(f.rule_id == "SEO_HEADING_HIERARCHY" for f in findings)


def test_multiple_h1():
    crawl = _crawl()
    pages = [_page(url="https://example.com/", observations={"title": "Page", "h1_count": 2, "h1_text": ["A", "B"]})]
    findings = analyze_crawl(crawl, pages)
    assert any(f.rule_id == "SEO_H1_MULTIPLE" for f in findings)


def test_canonical_missing_and_mismatch():
    crawl = _crawl()
    pages = [
        _page(url="https://example.com/", observations={"title": "Page title ok", "h1_count": 1}),
        _page(
            url="https://example.com/about",
            observations={"title": "About page ok", "h1_count": 1, "canonical": "https://other.com/about"},
        ),
    ]
    findings = analyze_crawl(crawl, pages)
    assert any(f.rule_id == "SEO_CANONICAL_MISSING" for f in findings)
    mismatch = [f for f in findings if f.rule_id == "SEO_CANONICAL_MISMATCH"]
    assert mismatch
    assert mismatch[0].severity == "WARNING"


def test_robots_noindex_observation():
    crawl = _crawl()
    pages = [_page(url="https://example.com/", observations={"title": "Page title ok", "h1_count": 1, "robots_meta": "noindex,nofollow"})]
    findings = analyze_crawl(crawl, pages)
    assert any(f.rule_id == "SEO_ROBOTS_META_NOINDEX" for f in findings)
    assert "Observed" in findings[0].title or any("Observed" in f.title for f in findings)


def test_robots_txt_blocked():
    crawl = _crawl()
    pages = [
        _page(url="https://example.com/robots.txt", observations={"robots_txt": True}, content_type="text/plain"),
        _page(url="https://example.com/secret", error_code="blocked_by_robots", observations={}),
    ]
    findings = analyze_crawl(crawl, pages)
    assert any(f.rule_id == "SEO_ROBOTS_TXT_BLOCKED_URLS" for f in findings)


def test_sitemap_not_observed():
    crawl = _crawl()
    pages = [_page(url="https://example.com/", observations={"title": "Page title ok", "h1_count": 1})]
    findings = analyze_crawl(crawl, pages)
    assert any(f.rule_id == "SEO_SITEMAP_NOT_OBSERVED" for f in findings)


def test_broken_internal_link():
    crawl = _crawl()
    broken = _page(url="https://example.com/broken", http_status=404, observations={})
    source = _page(
        url="https://example.com/",
        observations={"title": "Home page title", "h1_count": 1, "internal_links": ["https://example.com/broken"]},
    )
    findings = analyze_crawl(crawl, [source, broken])
    assert any(f.rule_id == "SEO_LINK_INTERNAL_4XX_5XX" for f in findings)


def test_redirect_chain():
    crawl = _crawl()
    pages = [_page(url="https://example.com/redir", redirect_count=3, observations={"title": "Redirect page ok", "h1_count": 1})]
    findings = analyze_crawl(crawl, pages)
    assert any(f.rule_id == "SEO_REDIRECT_CHAIN" for f in findings)


def test_orphan_page():
    crawl = _crawl()
    pages = [
        _page(url="https://example.com/", observations={"title": "Home page title", "h1_count": 1}),
        _page(url="https://example.com/orphan", depth=2, observations={"title": "Orphan page title", "h1_count": 1}),
    ]
    findings = analyze_crawl(crawl, pages)
    assert any(f.rule_id == "SEO_ORPHAN_PAGE" for f in findings)


def test_deep_page():
    crawl = _crawl()
    pages = [_page(url="https://example.com/deep", depth=8, observations={"title": "Deep page title", "h1_count": 1})]
    findings = analyze_crawl(crawl, pages)
    assert any(f.rule_id == "SEO_CRAWL_DEPTH_DEEP" for f in findings)


def test_image_alt_missing():
    crawl = _crawl()
    pages = [_page(url="https://example.com/", observations={"title": "Page title ok", "h1_count": 1, "images_total": 2, "images_missing_alt": 1})]
    findings = analyze_crawl(crawl, pages)
    assert any(f.rule_id == "SEO_IMAGE_ALT_MISSING" for f in findings)
    alt_finding = next(f for f in findings if f.rule_id == "SEO_IMAGE_ALT_MISSING")
    assert "decorative" in alt_finding.description.lower()


def test_structured_data_present():
    crawl = _crawl()
    pages = [_page(url="https://example.com/", observations={"title": "Page title ok", "h1_count": 1, "structured_data_blocks": 1})]
    findings = analyze_crawl(crawl, pages)
    assert any(f.rule_id == "SEO_STRUCTURED_DATA_PRESENT" for f in findings)


def test_hreflang_malformed():
    crawl = _crawl()
    pages = [_page(url="https://example.com/", observations={"title": "Page title ok", "h1_count": 1, "hreflang": [{"hreflang": "", "href": ""}]})]
    findings = analyze_crawl(crawl, pages)
    assert any(f.rule_id == "SEO_HREFLANG_MALFORMED" for f in findings)


def test_http_url():
    crawl = _crawl("http://example.com/")
    pages = [_page(url="http://example.com/", observations={"title": "Page title ok", "h1_count": 1})]
    findings = analyze_crawl(crawl, pages)
    assert any(f.rule_id == "SEO_HTTP_URL" for f in findings)


def test_unexpected_content_type():
    crawl = _crawl()
    pages = [_page(url="https://example.com/data.json", content_type="application/json", observations={"non_html": True, "content_type": "application/json"})]
    findings = analyze_crawl(crawl, pages)
    assert any(f.rule_id == "SEO_UNEXPECTED_CONTENT_TYPE" for f in findings)


def test_finding_deduplication():
    drafts = [
        FindingDraft(rule_id="SEO_TITLE_MISSING", category="title", severity="MEDIUM", title="A", description="A", url="https://example.com/"),
        FindingDraft(rule_id="SEO_TITLE_MISSING", category="title", severity="MEDIUM", title="A", description="A", url="https://example.com/"),
    ]
    deduped = _dedupe_findings(drafts)
    assert len(deduped) == 1


def test_severity_levels():
    crawl = _crawl()
    pages = [_page(url="https://example.com/error", http_status=503, observations={})]
    findings = analyze_crawl(crawl, pages)
    assert any(f.severity == "HIGH" for f in findings)


@pytest.mark.asyncio
async def test_analysis_api_and_tenant_isolation():
    email_a = f"a-{uuid.uuid4().hex[:6]}@t.com"
    email_b = f"b-{uuid.uuid4().hex[:6]}@t.com"
    async with AsyncSessionLocal() as db:
        org_a = Organization(name="A", slug=f"a-{uuid.uuid4().hex[:6]}", demo_mode=False)
        org_b = Organization(name="B", slug=f"b-{uuid.uuid4().hex[:6]}", demo_mode=False)
        user_a = User(email=email_a, hashed_password=hash_password("pass"), full_name="A")
        user_b = User(email=email_b, hashed_password=hash_password("pass"), full_name="B")
        db.add_all([org_a, org_b, user_a, user_b])
        await db.flush()
        db.add(OrganizationMember(organization_id=org_a.id, user_id=user_a.id, role=MemberRole.owner))
        db.add(OrganizationMember(organization_id=org_b.id, user_id=user_b.id, role=MemberRole.owner))
        crawl = SeoCrawl(
            organization_id=org_a.id,
            root_url="https://example.com/",
            status=SeoCrawlStatus.completed,
            config={},
            stats={},
        )
        db.add(crawl)
        await db.flush()
        db.add(
            SeoCrawlPage(
                crawl_id=crawl.id,
                organization_id=org_a.id,
                url="https://example.com/",
                depth=0,
                http_status=200,
                content_type="text/html",
                response_bytes=100,
                redirect_count=0,
                observations={"title": None, "h1_count": 0, "data_provenance": "http_crawl"},
                data_source="http_crawl",
            )
        )
        await db.commit()
        crawl_id = crawl.id

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        login_a = await client.post("/api/v1/auth/login", json={"email": email_a, "password": "pass"})
        headers_a = {"Authorization": f"Bearer {login_a.json()['access_token']}"}
        summary = await client.get(f"/api/v1/seo/crawls/{crawl_id}/summary", headers=headers_a)
        assert summary.status_code == 200
        body = summary.json()
        assert body["total_findings"] >= 1
        assert "disclaimer" in body

        findings = await client.get(f"/api/v1/seo/crawls/{crawl_id}/findings?severity=MEDIUM", headers=headers_a)
        assert findings.status_code == 200
        assert all(f["severity"] == "MEDIUM" for f in findings.json())

        login_b = await client.post("/api/v1/auth/login", json={"email": email_b, "password": "pass"})
        headers_b = {"Authorization": f"Bearer {login_b.json()['access_token']}"}
        denied = await client.get(f"/api/v1/seo/crawls/{crawl_id}/summary", headers=headers_b)
        assert denied.status_code == 404
