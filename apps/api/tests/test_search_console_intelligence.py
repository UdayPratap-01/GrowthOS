"""M9.3 — Search Console intelligence tests."""

from __future__ import annotations

import json
import uuid
from datetime import date, timedelta
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from app.core.security import hash_password
from app.db.session import AsyncSessionLocal
from app.integrations.persistence import upsert_integration
from app.models.enums import MemberRole
from app.models.organization import Organization, OrganizationMember
from app.models.user import User
from app.seo.search_console.comparison import pct_change, position_change
from app.seo.search_console.dates import resolve_preset_range
from app.seo.search_console.intelligence import detect_opportunities
from app.services.search_console_intelligence_service import SearchConsoleIntelligenceService
from app.main import app


def test_pct_change_zero_baseline():
    result = pct_change(10, 0)
    assert result["change_label"] == "increase_from_zero_baseline"
    assert result["change_pct"] is None


def test_pct_change_missing_previous():
    assert pct_change(10, None)["change_label"] == "no_previous_data"


def test_position_change_improved():
    result = position_change(5.0, 8.0)
    assert result["change_label"] == "improved"
    assert result["position_change"] == 3.0


def test_date_range_latency_note():
    today = date(2026, 9, 27)
    dr = resolve_preset_range("last_7_days", today=today)
    assert dr.end <= today
    assert "latency" in dr.note.lower()


def test_low_ctr_query_opportunity():
    rows = [
        {
            "query": "widgets",
            "page": None,
            "clicks": 2,
            "impressions": 1000,
            "ctr": 0.002,
            "position": 12.0,
        }
    ]
    opps = detect_opportunities(rows)
    rules = {o.rule_id for o in opps}
    assert "GSC_OPP_LOW_CTR_QUERY" in rules
    assert "GSC_OPP_PAGE_BOUNDARY" in rules


def test_declining_clicks_page():
    rows = [
        {
            "query": None,
            "page": "https://example.com/a",
            "clicks": 10,
            "impressions": 500,
            "ctr": 0.02,
            "position": 5.0,
            "compare_clicks": 20,
            "compare_impressions": 500,
            "compare_ctr": 0.04,
            "compare_position": 5.0,
        }
    ]
    opps = detect_opportunities(rows)
    assert any(o.rule_id == "GSC_OPP_DECLINING_CLICKS_PAGE" for o in opps)


@pytest.mark.asyncio
async def test_sync_success_idempotent(monkeypatch):
    org_id = uuid.uuid4()
    async with AsyncSessionLocal() as db:
        org = Organization(name="GSC", slug=f"gsc-{uuid.uuid4().hex[:6]}", demo_mode=False)
        db.add(org)
        await db.flush()
        org_id = org.id
        await upsert_integration(
            db,
            organization_id=org_id,
            provider="google_search_console",
            client_id=None,
            status="connected",
            config={"site_url": "https://example.com/"},
            token_payload={"access_token": "tok", "provider": "google_search_console"},
        )
        await db.commit()

    fake_rows = [
        {"query": "seo tools", "clicks": 5.0, "impressions": 500.0, "ctr": 0.01, "position": 9.0},
    ]

    async def fake_query(self, site_url, *, start_date, end_date, dimensions, row_limit=250, start_row=0):
        if not dimensions:
            return [{"clicks": 5.0, "impressions": 500.0, "ctr": 0.01, "position": 9.0}]
        if dimensions == ["query"]:
            return fake_rows
        if dimensions == ["page"]:
            return [{"page": "https://example.com/", "clicks": 5.0, "impressions": 500.0, "ctr": 0.01, "position": 9.0}]
        return [
            {
                "query": "seo tools",
                "page": "https://example.com/",
                "clicks": 5.0,
                "impressions": 500.0,
                "ctr": 0.01,
                "position": 9.0,
            }
        ]

    async def fake_fetch_all(self, site_url, *, start_date, end_date, dimensions, max_rows=500):
        return await fake_query(self, site_url, start_date=start_date, end_date=end_date, dimensions=dimensions)

    with patch("app.services.search_console_intelligence_service.ensure_access_token", AsyncMock(return_value="tok")):
        with patch("app.seo.search_console.client.SearchConsoleClient.fetch_all_rows", fake_fetch_all):
            with patch("app.seo.search_console.client.SearchConsoleClient.query_search_analytics", fake_query):
                async with AsyncSessionLocal() as db:
                    svc = SearchConsoleIntelligenceService(db)
                    sync1 = await svc.run_sync(organization_id=org_id, preset="last_28_days")
                    await db.commit()
                    sync1_id = sync1.id
                    row_count = sync1.row_count
                    opp_count = sync1.opportunity_count

                async with AsyncSessionLocal() as db:
                    svc = SearchConsoleIntelligenceService(db)
                    sync2 = await svc.run_sync(organization_id=org_id, preset="last_28_days")
                    assert sync2.id == sync1_id
                    assert sync2.row_count == row_count
                    assert sync2.opportunity_count == opp_count


@pytest.mark.asyncio
async def test_sync_api_failure(monkeypatch):
    org_id = uuid.uuid4()
    async with AsyncSessionLocal() as db:
        org = Organization(name="GSC2", slug=f"gsc2-{uuid.uuid4().hex[:6]}", demo_mode=False)
        db.add(org)
        await db.flush()
        org_id = org.id
        await upsert_integration(
            db,
            organization_id=org_id,
            provider="google_search_console",
            client_id=None,
            status="connected",
            config={"site_url": "https://example.com/"},
            token_payload={"access_token": "tok", "provider": "google_search_console"},
        )
        await db.commit()

    async def boom(*args, **kwargs):
        raise RuntimeError("Search analytics query failed: HTTP 500")

    from fastapi import HTTPException

    with patch("app.services.search_console_intelligence_service.ensure_access_token", AsyncMock(return_value="tok")):
        with patch("app.seo.search_console.client.SearchConsoleClient.query_search_analytics", boom):
            async with AsyncSessionLocal() as db:
                svc = SearchConsoleIntelligenceService(db)
                with pytest.raises(HTTPException) as exc:
                    await svc.run_sync(organization_id=org_id, preset="last_28_days")
                assert exc.value.status_code == 502


@pytest.mark.asyncio
async def test_gsc_intelligence_tenant_isolation():
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
        await upsert_integration(
            db,
            organization_id=org_a.id,
            provider="google_search_console",
            client_id=None,
            status="connected",
            config={"site_url": "https://tenant-a.example/"},
            token_payload={"access_token": "tok-a", "provider": "google_search_console"},
        )
        await db.commit()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        login_b = await client.post("/api/v1/auth/login", json={"email": email_b, "password": "pass"})
        headers_b = {"Authorization": f"Bearer {login_b.json()['access_token']}"}
        denied = await client.get("/api/v1/seo/search-console/summary", headers=headers_b)
        assert denied.status_code == 200
        assert denied.json()["connected"] is False

        login_a = await client.post("/api/v1/auth/login", json={"email": email_a, "password": "pass"})
        headers_a = {"Authorization": f"Bearer {login_a.json()['access_token']}"}
        ok = await client.get("/api/v1/seo/search-console/summary", headers=headers_a)
        assert ok.status_code == 200
        assert ok.json()["connected"] is True
        assert "access_token" not in json.dumps(ok.json())
