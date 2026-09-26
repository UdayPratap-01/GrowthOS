"""M9.4 — Keyword opportunity engine tests."""

from __future__ import annotations

import uuid
from datetime import date

import pytest
from httpx import ASGITransport, AsyncClient

from app.core.security import hash_password
from app.db.session import AsyncSessionLocal
from app.models.enums import MemberRole, SearchConsoleSyncStatus
from app.models.organization import Organization, OrganizationMember
from app.models.search_console import SearchConsolePerformanceRow, SearchConsoleSync
from app.models.user import User
from app.seo.keywords.aggregate import aggregate_performance_rows
from app.seo.keywords.normalize import normalize_query
from app.seo.keywords.priority import compute_priority_score
from app.seo.keywords.rules import classify_branded, detect_keyword_opportunities
from app.services.keyword_opportunity_service import KeywordOpportunityService
from app.main import app


def test_normalize_whitespace_and_case():
    assert normalize_query("  Hello   WORLD  ") == "hello world"


def test_unicode_normalization():
    assert normalize_query("café") == "café"


def test_branded_unknown_without_terms():
    assert classify_branded("growthos pricing", None) == "unknown"


def test_branded_detection():
    assert classify_branded("growthos pricing", ["GrowthOS"]) == "branded"


def test_priority_formula_deterministic():
    p1, s1 = compute_priority_score(impressions=1000, ctr=0.01, average_position=9.0, low_ctr=0.02)
    p2, s2 = compute_priority_score(impressions=1000, ctr=0.01, average_position=9.0, low_ctr=0.02)
    assert p1 == p2 and s1 == s2


def test_high_impression_low_ctr_rule():
    rows = [
        type("R", (), {
            "dimension_type": "query_page",
            "query": "seo tools",
            "page_url": "https://example.com/",
            "clicks": 2.0,
            "impressions": 500.0,
            "ctr": 0.004,
            "average_position": 9.0,
            "compare_clicks": 1.0,
            "compare_impressions": 400.0,
            "compare_ctr": 0.0025,
            "compare_position": 10.0,
            "metrics_delta": {},
        })()
    ]
    queries, qps = aggregate_performance_rows(rows)
    opps = detect_keyword_opportunities(queries, qps)
    assert any(o.rule_id == "KW_OPP_HIGH_IMPRESSION_LOW_CTR" for o in opps)


def test_multi_page_ranking_signal():
    rows = [
        _perf_row("q", "https://example.com/a", 100, 1, 0.01, 8),
        _perf_row("q", "https://example.com/b", 120, 2, 0.016, 9),
    ]
    queries, qps = aggregate_performance_rows(rows)
    opps = detect_keyword_opportunities(queries, qps)
    assert any(o.rule_id == "KW_OPP_MULTI_PAGE_RANKING" for o in opps)


def test_zero_impressions_no_opportunity():
    rows = [_perf_row("empty", "https://example.com/", 0, 0, 0, 50)]
    queries, qps = aggregate_performance_rows(rows)
    opps = detect_keyword_opportunities(queries, qps)
    assert not opps


def _perf_row(query, page, impressions, clicks, ctr, position):
    return type("R", (), {
        "dimension_type": "query_page",
        "query": query,
        "page_url": page,
        "clicks": float(clicks),
        "impressions": float(impressions),
        "ctr": float(ctr),
        "average_position": float(position),
        "compare_clicks": None,
        "compare_impressions": None,
        "compare_ctr": None,
        "compare_position": None,
        "metrics_delta": {},
    })()


@pytest.mark.asyncio
async def test_analyze_idempotent():
    org_id = uuid.uuid4()
    async with AsyncSessionLocal() as db:
        org = Organization(name="KW", slug=f"kw-{uuid.uuid4().hex[:6]}", demo_mode=False)
        db.add(org)
        await db.flush()
        org_id = org.id
        sync = SearchConsoleSync(
            organization_id=org_id,
            site_url="https://example.com/",
            sync_key="test-key",
            status=SearchConsoleSyncStatus.completed,
            requested_start_date=date(2026, 8, 1),
            requested_end_date=date(2026, 8, 28),
            effective_start_date=date(2026, 8, 1),
            effective_end_date=date(2026, 8, 28),
            meta={},
        )
        db.add(sync)
        await db.flush()
        db.add(
            SearchConsolePerformanceRow(
                sync_id=sync.id,
                organization_id=org_id,
                site_url="https://example.com/",
                dimension_type="query_page",
                query="seo audit",
                page_url="https://example.com/seo",
                start_date=date(2026, 8, 1),
                end_date=date(2026, 8, 28),
                clicks=3,
                impressions=600,
                ctr=0.005,
                average_position=9.5,
                compare_clicks=2,
                compare_impressions=500,
                compare_ctr=0.004,
                compare_position=10.0,
                metrics_delta={},
                row_key="k1",
            )
        )
        await db.commit()
        sync_id = sync.id

    async with AsyncSessionLocal() as db:
        svc = KeywordOpportunityService(db)
        r1 = await svc.analyze(organization_id=org_id, sync_id=sync_id)
        await db.commit()
        count1 = r1["opportunities_created"]

    async with AsyncSessionLocal() as db:
        svc = KeywordOpportunityService(db)
        r2 = await svc.analyze(organization_id=org_id, sync_id=sync_id)
        await db.commit()
        assert r2["opportunities_created"] == count1


@pytest.mark.asyncio
async def test_keyword_api_tenant_isolation():
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
        sync = SearchConsoleSync(
            organization_id=org_a.id,
            site_url="https://a.example/",
            sync_key="a-key",
            status=SearchConsoleSyncStatus.completed,
            requested_start_date=date(2026, 8, 1),
            requested_end_date=date(2026, 8, 28),
            effective_start_date=date(2026, 8, 1),
            effective_end_date=date(2026, 8, 28),
            meta={},
        )
        db.add(sync)
        await db.commit()
        sync_id = sync.id

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        login_b = await client.post("/api/v1/auth/login", json={"email": email_b, "password": "pass"})
        headers_b = {"Authorization": f"Bearer {login_b.json()['access_token']}"}
        denied = await client.get(f"/api/v1/seo/keywords/summary?sync_id={sync_id}", headers=headers_b)
        assert denied.status_code == 404
