"""M9.6 — Competitor and content-gap analysis tests."""

from __future__ import annotations

import uuid
from datetime import date, datetime, timezone

import pytest
from fastapi import HTTPException
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select

from app.core.security import hash_password
from app.db.session import AsyncSessionLocal
from app.main import app
from app.models.enums import MemberRole, SearchConsoleSyncStatus, SeoCrawlStatus
from app.models.keyword_opportunity import KeywordOpportunity
from app.models.organization import Organization, OrganizationMember
from app.models.search_console import SearchConsolePerformanceRow, SearchConsoleSync
from app.models.seo_competitor import ContentGap, SeoCompetitor, SeoCompetitorCrawl, SeoCompetitorPage
from app.models.topic_cluster import TopicCluster
from app.models.user import User
from app.seo.content_gap.engine import GapAnalysisInput, detect_content_gaps
from app.seo.content_gap.matching import classify_strength
from app.seo.content_gap.representation import extract_page_representation
from app.seo.content_gap.thresholds import ALGORITHM_VERSION
from app.seo.ssrf import SsrfError, validate_url_target
from app.services.content_gap_service import ContentGapService
from app.services.keyword_opportunity_service import KeywordOpportunityService
from app.services.seo_competitor_service import SeoCompetitorService
from app.services.topic_clustering_service import TopicClusteringService


def test_title_h1_heading_extraction():
    rep = extract_page_representation(
        url="https://competitor.example/page",
        observations={"title": "SEO Audit Guide", "h1_text": ["Complete SEO Audit"], "h2_text": ["Checklist"]},
    )
    assert rep.title == "SEO Audit Guide"
    assert rep.h1 == "Complete SEO Audit"
    assert "seo" in rep.tokens
    assert rep.topic_label == "Complete SEO Audit"


def test_strong_lexical_match():
    assert classify_strength(0.5) == "strong_match"
    assert classify_strength(0.3) == "moderate_match"
    assert classify_strength(0.2) == "weak_match"
    assert classify_strength(0.05) == "no_match"


def test_gap_engine_no_user_topics():
    drafts = detect_content_gaps(
        GapAnalysisInput(
            user_topics=[],
            opportunities=[],
            competitor_pages=[
                {
                    "url": "https://competitor.example/new-topic",
                    "observations": {"title": "Brand New Topic", "h1_text": ["Brand New Topic"]},
                }
            ],
        )
    )
    assert drafts
    assert drafts[0].gap_type == "COMPETITOR_TOPIC_NO_USER_CLUSTER"


def test_gap_engine_strong_match_depth_signal():
    user_topics = [
        {
            "id": "t1",
            "topic_label": "seo audit",
            "representative_query": "seo audit checklist",
            "query_count": 2,
            "page_count": 1,
            "total_impressions": 500,
            "tokens": {"seo", "audit", "checklist", "guide"},
        }
    ]
    pages = [
        {
            "url": "https://competitor.example/audit-1",
            "observations": {"title": "SEO Audit Checklist", "h1_text": ["SEO Audit Checklist"]},
            "competitor_id": "c1",
        },
        {
            "url": "https://competitor.example/audit-2",
            "observations": {"title": "SEO Audit Guide", "h1_text": ["SEO Audit Guide"]},
            "competitor_id": "c1",
        },
    ]
    drafts = detect_content_gaps(GapAnalysisInput(user_topics=user_topics, opportunities=[], competitor_pages=pages))
    types = {d.gap_type for d in drafts}
    assert "USER_TOPIC_COMPETITOR_DEPTH" in types or "MULTI_COMPETITOR_LIMITED_USER" in types


def test_no_fabricated_metrics_in_evidence():
    drafts = detect_content_gaps(
        GapAnalysisInput(
            user_topics=[{"id": "1", "topic_label": "x", "representative_query": "x", "query_count": 1, "page_count": 1, "total_impressions": 1, "tokens": {"x"}}],
            opportunities=[],
            competitor_pages=[{"url": "https://c.example/", "observations": {"title": "y", "h1_text": ["y"]}}],
        )
    )
    for d in drafts:
        assert "rank" not in d.explanation.lower()
        assert d.evidence.get("data_honesty")


@pytest.mark.asyncio
async def test_localhost_blocked_on_competitor_create():
    org_id = uuid.uuid4()
    async with AsyncSessionLocal() as db:
        org = Organization(name="CG", slug=f"cg-{uuid.uuid4().hex[:6]}", demo_mode=False)
        db.add(org)
        await db.commit()
        org_id = org.id
    async with AsyncSessionLocal() as db:
        svc = SeoCompetitorService(db)
        with pytest.raises(HTTPException) as exc:
            await svc.create_competitor(organization_id=org_id, root_url="http://localhost/")
        assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_invalid_scheme_blocked():
    org_id = uuid.uuid4()
    async with AsyncSessionLocal() as db:
        org = Organization(name="CG2", slug=f"cg2-{uuid.uuid4().hex[:6]}", demo_mode=False)
        db.add(org)
        await db.commit()
        org_id = org.id
    async with AsyncSessionLocal() as db:
        svc = SeoCompetitorService(db)
        with pytest.raises(HTTPException):
            await svc.create_competitor(organization_id=org_id, root_url="javascript:alert(1)")


@pytest.mark.asyncio
async def test_private_ip_blocked():
    with pytest.raises(SsrfError):
        await validate_url_target("http://10.0.0.1/")


async def _seed_full_pipeline():
    org_id = uuid.uuid4()
    async with AsyncSessionLocal() as db:
        org = Organization(name="Gap", slug=f"gap-{uuid.uuid4().hex[:6]}", demo_mode=False)
        db.add(org)
        await db.flush()
        org_id = org.id
        sync = SearchConsoleSync(
            organization_id=org_id,
            site_url="https://example.com/",
            sync_key=f"key-{uuid.uuid4().hex[:8]}",
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
                query="seo audit checklist",
                page_url="https://example.com/audit",
                start_date=date(2026, 8, 1),
                end_date=date(2026, 8, 28),
                clicks=3,
                impressions=600,
                ctr=0.005,
                average_position=9.0,
                row_key="r1",
                metrics_delta={},
            )
        )
        db.add(
            SearchConsolePerformanceRow(
                sync_id=sync.id,
                organization_id=org_id,
                site_url="https://example.com/",
                dimension_type="query_page",
                query="email marketing tips",
                page_url="https://example.com/email",
                start_date=date(2026, 8, 1),
                end_date=date(2026, 8, 28),
                clicks=1,
                impressions=200,
                ctr=0.005,
                average_position=12.0,
                row_key="r2",
                metrics_delta={},
            )
        )
        await db.commit()
        await KeywordOpportunityService(db).analyze(organization_id=org_id, sync_id=sync.id)
        await TopicClusteringService(db).analyze(organization_id=org_id, sync_id=sync.id)
        await db.commit()

        competitor = SeoCompetitor(
            organization_id=org_id,
            display_name="Rival",
            root_url="https://competitor.example/",
            domain="competitor.example",
            status="active",
        )
        db.add(competitor)
        await db.flush()
        crawl = SeoCompetitorCrawl(
            competitor_id=competitor.id,
            organization_id=org_id,
            root_url=competitor.root_url,
            status=SeoCrawlStatus.completed,
            config={},
            stats={},
            completed_at=datetime.now(timezone.utc),
        )
        db.add(crawl)
        await db.flush()
        db.add(
            SeoCompetitorPage(
                crawl_id=crawl.id,
                competitor_id=competitor.id,
                organization_id=org_id,
                url="https://competitor.example/seo-audit",
                title="Complete SEO Audit Guide",
                h1="Complete SEO Audit Guide",
                headings=["Complete SEO Audit Guide", "Checklist"],
                topic_label="Complete SEO Audit Guide",
                token_terms=["seo", "audit", "guide", "complete"],
                observations={"title": "Complete SEO Audit Guide", "h1_text": ["Complete SEO Audit Guide"]},
                fetched_at=datetime.now(timezone.utc),
            )
        )
        db.add(
            SeoCompetitorPage(
                crawl_id=crawl.id,
                competitor_id=competitor.id,
                organization_id=org_id,
                url="https://competitor.example/email-marketing",
                title="Email Marketing Masterclass",
                h1="Email Marketing Masterclass",
                headings=["Email Marketing Masterclass"],
                topic_label="Email Marketing Masterclass",
                token_terms=["email", "marketing", "masterclass"],
                observations={"title": "Email Marketing Masterclass", "h1_text": ["Email Marketing Masterclass"]},
                fetched_at=datetime.now(timezone.utc),
            )
        )
        await db.commit()
        return org_id, sync.id, competitor.id


@pytest.mark.asyncio
async def test_content_gap_analyze_and_idempotent():
    org_id, sync_id, _ = await _seed_full_pipeline()
    async with AsyncSessionLocal() as db:
        svc = ContentGapService(db)
        r1 = await svc.analyze(organization_id=org_id, sync_id=sync_id)
        await db.commit()
        count1 = await db.scalar(select(func.count()).select_from(ContentGap).where(ContentGap.organization_id == org_id))
        r2 = await svc.analyze(organization_id=org_id, sync_id=sync_id)
        await db.commit()
        count2 = await db.scalar(select(func.count()).select_from(ContentGap).where(ContentGap.organization_id == org_id))
    assert r1["gaps_created"] > 0
    assert r1["algorithm_version"] == ALGORITHM_VERSION
    assert count1 == count2


@pytest.mark.asyncio
async def test_insufficient_competitor_data():
    org_id, sync_id, _ = await _seed_full_pipeline()
    async with AsyncSessionLocal() as db:
        await db.execute(select(SeoCompetitorPage))
        pages = (await db.execute(select(SeoCompetitorPage))).scalars().all()
        for p in pages:
            await db.delete(p)
        await db.commit()
    async with AsyncSessionLocal() as db:
        with pytest.raises(HTTPException) as exc:
            await ContentGapService(db).analyze(organization_id=org_id, sync_id=sync_id)
        assert exc.value.detail == "insufficient_competitor_data"


@pytest.mark.asyncio
async def test_tenant_isolation():
    org_id, sync_id, comp_id = await _seed_full_pipeline()
    other = uuid.uuid4()
    async with AsyncSessionLocal() as db:
        svc = ContentGapService(db)
        await svc.analyze(organization_id=org_id, sync_id=sync_id)
        await db.commit()
        gaps = await svc.list_gaps(organization_id=org_id, sync_id=sync_id)
        with pytest.raises(HTTPException):
            await svc.get_gap(organization_id=other, gap_id=gaps[0].id)
        with pytest.raises(HTTPException):
            await SeoCompetitorService(db).get_competitor(organization_id=other, competitor_id=comp_id)


@pytest.mark.asyncio
async def test_api_competitors_and_gaps():
    org_id, sync_id, _ = await _seed_full_pipeline()
    email = f"cg-{uuid.uuid4().hex[:8]}@example.com"
    async with AsyncSessionLocal() as db:
        user = User(email=email, hashed_password=hash_password("secret123"), full_name="Gap Tester")
        db.add(user)
        await db.flush()
        db.add(OrganizationMember(organization_id=org_id, user_id=user.id, role=MemberRole.owner))
        await db.commit()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        login = await client.post("/api/v1/auth/login", json={"email": email, "password": "secret123"})
        headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
        competitors = await client.get("/api/v1/seo/competitors", headers=headers)
        assert competitors.status_code == 200
        analyze = await client.post("/api/v1/seo/content-gaps/analyze", headers=headers, json={})
        assert analyze.status_code == 200
        gaps = await client.get("/api/v1/seo/content-gaps?limit=20", headers=headers)
        assert gaps.status_code == 200
        assert len(gaps.json()) >= 1
        summary = await client.get("/api/v1/seo/content-gaps/summary", headers=headers)
        assert summary.status_code == 200
        assert summary.json()["has_competitor_data"] is True
