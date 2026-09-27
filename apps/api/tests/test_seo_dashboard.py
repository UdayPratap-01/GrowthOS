"""M9.14 — SEO dashboard aggregation tests."""

from __future__ import annotations

import uuid

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.db.session import AsyncSessionLocal
from app.main import app
from app.models.seo import SeoCrawl
from app.services.seo_dashboard_service import SeoDashboardService
from tests.test_seo_internal_links import _seed_crawl
from tests.test_seo_onpage_optimizer import _auth_client, _seed_content


@pytest.mark.asyncio
async def test_empty_organization_dashboard():
    org_id, _, _ = await _seed_content()
    async with AsyncSessionLocal() as db:
        dash = await SeoDashboardService(db).get_dashboard(organization_id=org_id)
    assert dash.overview.pending_actions == 0
    assert dash.actions.pending == 0
    assert "does not trigger crawls" in dash.disclaimer.lower()


@pytest.mark.asyncio
async def test_dashboard_with_crawl():
    org_id, _, _ = await _seed_content()
    async with AsyncSessionLocal() as db:
        await _seed_crawl(db, org_id)
        crawl = await db.scalar(select(SeoCrawl).where(SeoCrawl.organization_id == org_id))
        assert crawl is not None
        dash = await SeoDashboardService(db).get_dashboard(organization_id=org_id)
    assert dash.overview.latest_crawl_id is not None
    assert dash.technical.available is True
    assert dash.technical.crawl_id == dash.overview.latest_crawl_id


@pytest.mark.asyncio
async def test_dashboard_with_internal_links():
    org_id, _, content = await _seed_content()
    async with AsyncSessionLocal() as db:
        await _seed_crawl(db, org_id)
        from app.services.seo_internal_link_service import SeoInternalLinkService

        await SeoInternalLinkService(db).generate(
            organization_id=org_id, content_id=content.id, use_ai=False
        )
        await db.commit()
        dash = await SeoDashboardService(db).get_dashboard(organization_id=org_id)
    assert dash.internal_links.available is True
    assert dash.content.content_with_internal_links >= 1


@pytest.mark.asyncio
async def test_tenant_isolation_dashboard_api():
    org_a, _, _ = await _seed_content()
    org_b, _, _ = await _seed_content()
    async with AsyncSessionLocal() as db:
        await _seed_crawl(db, org_a)
        await db.commit()
    client_a = await _auth_client(org_a)
    client_b = await _auth_client(org_b)
    resp_a = await client_a.get("/api/v1/seo/dashboard")
    resp_b = await client_b.get("/api/v1/seo/dashboard")
    assert resp_a.status_code == 200
    assert resp_b.status_code == 200
    body_a = resp_a.json()
    body_b = resp_b.json()
    crawl_id_a = body_a["overview"]["latest_crawl_id"]
    assert crawl_id_a is not None
    assert body_a["technical"]["crawl_id"] == crawl_id_a
    assert body_b["overview"].get("latest_crawl_id") != crawl_id_a
    async with AsyncSessionLocal() as db:
        crawl_row = await db.get(SeoCrawl, uuid.UUID(crawl_id_a))
        assert crawl_row is not None
        assert crawl_row.organization_id == org_a


@pytest.mark.asyncio
async def test_dashboard_api_returns_sections():
    org_id, _, content = await _seed_content()
    async with AsyncSessionLocal() as db:
        await _seed_crawl(db, org_id)
        await db.commit()
    client = await _auth_client(org_id)
    resp = await client.get("/api/v1/seo/dashboard")
    assert resp.status_code == 200
    body = resp.json()
    for section in (
        "overview",
        "technical",
        "search_console",
        "keywords",
        "topics",
        "competitors",
        "content",
        "on_page",
        "schema_panel",
        "internal_links",
        "actions",
        "attention",
        "monitoring",
    ):
        assert section in body
    assert "disclaimer" in body


@pytest.mark.asyncio
async def test_dashboard_does_not_trigger_side_effects():
    """Dashboard load must not create crawls, actions, or sync jobs."""
    org_id, _, _ = await _seed_content()
    async with AsyncSessionLocal() as db:
        crawl_before = await db.scalar(
            select(SeoCrawl).where(SeoCrawl.organization_id == org_id)
        )
        await SeoDashboardService(db).get_dashboard(organization_id=org_id)
        crawl_after = await db.scalar(
            select(SeoCrawl).where(SeoCrawl.organization_id == org_id)
        )
    assert crawl_before is crawl_after


@pytest.mark.asyncio
async def test_attention_items_bounded():
    org_id, _, _ = await _seed_content()
    async with AsyncSessionLocal() as db:
        await _seed_crawl(db, org_id)
        dash = await SeoDashboardService(db).get_dashboard(organization_id=org_id)
    assert len(dash.attention.items) <= 20


def test_dashboard_disclaimer_no_autonomous_execution():
    from app.services.seo_dashboard_service import DISCLAIMER

    assert "autonomous" in DISCLAIMER.lower()
    assert "does not trigger" in DISCLAIMER.lower()
