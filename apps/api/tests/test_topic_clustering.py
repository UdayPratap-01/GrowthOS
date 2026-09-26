"""M9.5 — Topic clustering engine tests."""

from __future__ import annotations

import uuid
from datetime import date

import pytest
from fastapi import HTTPException
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select

from app.core.security import hash_password
from app.db.session import AsyncSessionLocal
from app.models.enums import KeywordOpportunityPriority, MemberRole, SearchConsoleSyncStatus
from app.models.keyword_opportunity import KeywordOpportunity
from app.models.organization import Organization, OrganizationMember
from app.models.search_console import SearchConsolePerformanceRow, SearchConsoleSync
from app.models.topic_cluster import TopicCluster
from app.models.user import User
from app.seo.keywords.normalize import normalize_query
from app.seo.topics.cluster import build_query_records, cluster_queries
from app.seo.topics.similarity import jaccard_similarity
from app.seo.topics.thresholds import ALGORITHM_VERSION, DEFAULT_TOPIC_THRESHOLDS, TopicClusterThresholds
from app.seo.topics.tokens import tokenize_query
from app.services.keyword_opportunity_service import KeywordOpportunityService
from app.services.topic_clustering_service import TopicClusteringService
from app.main import app


def test_normalize_reuse():
    assert normalize_query("  SEO   Tools  ") == "seo tools"


def test_unicode_query():
    rows = [{"query": "café marketing", "clicks": 1, "impressions": 10, "average_position": 5}]
    recs = build_query_records(rows)
    assert recs[0].normalized_query == normalize_query("café marketing")


def test_identical_queries_dedupe():
    rows = [
        {"query": "seo tools", "clicks": 1, "impressions": 10, "average_position": 5},
        {"query": "seo tools", "clicks": 2, "impressions": 20, "average_position": 6},
    ]
    recs = build_query_records(rows)
    assert len(recs) == 1
    assert recs[0].clicks == 3


def test_whitespace_variants_cluster():
    rows = [
        {"query": "seo  tools", "clicks": 1, "impressions": 50, "average_position": 8},
        {"query": "SEO Tools", "clicks": 2, "impressions": 60, "average_position": 9},
    ]
    drafts = cluster_queries(build_query_records(rows))
    assert len(drafts) == 1


def test_similar_queries_cluster():
    rows = [
        {"query": "best seo tools", "clicks": 1, "impressions": 100, "average_position": 8},
        {"query": "top seo tools", "clicks": 2, "impressions": 80, "average_position": 9},
    ]
    drafts = cluster_queries(build_query_records(rows))
    assert len(drafts) == 1
    assert len(drafts[0].queries) == 2


def test_unrelated_queries_separate():
    rows = [
        {"query": "seo tools", "clicks": 1, "impressions": 100, "average_position": 8},
        {"query": "email marketing software", "clicks": 2, "impressions": 80, "average_position": 9},
    ]
    drafts = cluster_queries(build_query_records(rows))
    assert len(drafts) == 2


def test_singleton_handling():
    rows = [{"query": "unique long tail phrase", "clicks": 1, "impressions": 10, "average_position": 15}]
    drafts = cluster_queries(build_query_records(rows))
    assert len(drafts) == 1
    assert drafts[0].is_singleton is True


def test_threshold_boundary():
    a = tokenize_query("alpha beta gamma")
    b = tokenize_query("alpha beta delta")
    sim = jaccard_similarity(a, b)
    assert 0.3 < sim < 0.6
    low = TopicClusterThresholds(min_jaccard_similarity=0.99)
    drafts = cluster_queries(
        build_query_records(
            [
                {"query": "alpha beta gamma", "clicks": 1, "impressions": 10, "average_position": 5},
                {"query": "alpha beta delta", "clicks": 1, "impressions": 10, "average_position": 5},
            ]
        ),
        thresholds=low,
    )
    assert len(drafts) == 2


def test_cluster_merging_transitive():
    rows = [
        {"query": "seo audit checklist", "clicks": 1, "impressions": 50, "average_position": 8},
        {"query": "seo audit guide", "clicks": 1, "impressions": 40, "average_position": 9},
        {"query": "seo audit template", "clicks": 1, "impressions": 30, "average_position": 10},
    ]
    drafts = cluster_queries(build_query_records(rows))
    assert len(drafts) == 1
    assert len(drafts[0].queries) == 3


def test_deterministic_cluster_keys():
    rows = [
        {"query": "seo tools", "clicks": 1, "impressions": 50, "average_position": 8},
        {"query": "best seo tools", "clicks": 1, "impressions": 40, "average_position": 9},
    ]
    d1 = cluster_queries(build_query_records(rows))
    d2 = cluster_queries(build_query_records(rows))
    assert d1[0].cluster_key == d2[0].cluster_key


def test_topic_label_deterministic():
    rows = [
        {"query": "seo audit checklist", "clicks": 1, "impressions": 50, "average_position": 8},
        {"query": "seo audit guide", "clicks": 1, "impressions": 40, "average_position": 9},
    ]
    d1 = cluster_queries(build_query_records(rows))
    d2 = cluster_queries(build_query_records(rows))
    assert d1[0].topic_label == d2[0].topic_label
    assert "seo" in d1[0].topic_label or "audit" in d1[0].topic_label


def test_representative_query_highest_impressions():
    rows = [
        {"query": "low imp query seo tools", "clicks": 1, "impressions": 10, "average_position": 8},
        {"query": "high imp seo tools", "clicks": 5, "impressions": 500, "average_position": 6},
    ]
    draft = cluster_queries(build_query_records(rows))[0]
    assert draft.representative_query == "high imp seo tools"


def test_performance_aggregation():
    rows = [
        {"query": "seo tools", "clicks": 10, "impressions": 100, "average_position": 8},
        {"query": "best seo tools", "clicks": 5, "impressions": 50, "average_position": 10},
    ]
    draft = cluster_queries(build_query_records(rows))[0]
    assert draft.total_clicks == 15
    assert draft.total_impressions == 150
    assert draft.aggregate_ctr == pytest.approx(0.1)


def test_zero_impressions():
    rows = [{"query": "no impressions", "clicks": 0, "impressions": 0, "average_position": 0}]
    draft = cluster_queries(build_query_records(rows))[0]
    assert draft.aggregate_ctr == 0.0


def test_multi_page_signal():
    rows = [
        {"query": "seo tools", "page_url": "https://example.com/a", "clicks": 1, "impressions": 50, "average_position": 8},
        {"query": "seo tools", "page_url": "https://example.com/b", "clicks": 2, "impressions": 60, "average_position": 9},
    ]
    draft = cluster_queries(build_query_records(rows))[0]
    assert draft.multi_page_signal is True
    assert draft.page_count == 2


def test_single_page_topic():
    rows = [
        {"query": "seo tools", "page_url": "https://example.com/a", "clicks": 1, "impressions": 50, "average_position": 8},
    ]
    draft = cluster_queries(build_query_records(rows))[0]
    assert draft.multi_page_signal is False
    assert draft.page_count == 1


def test_resource_limit_cap():
    rows = [{"query": f"query {i}", "clicks": 1, "impressions": i, "average_position": 5} for i in range(50)]
    th = TopicClusterThresholds(max_queries_per_analysis=10)
    drafts = cluster_queries(build_query_records(rows), thresholds=th)
    assert sum(len(d.queries) for d in drafts) <= 10


async def _seed_org_with_data():
    org_id = uuid.uuid4()
    async with AsyncSessionLocal() as db:
        org = Organization(name="Topics", slug=f"topics-{uuid.uuid4().hex[:6]}", demo_mode=False)
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
        perf = [
            ("seo audit checklist", "https://example.com/audit", 600, 3, 9.0),
            ("seo audit guide", "https://example.com/guide", 400, 2, 10.0),
            ("email marketing tips", "https://example.com/email", 200, 1, 12.0),
        ]
        for i, (q, page, imp, clicks, pos) in enumerate(perf):
            db.add(
                SearchConsolePerformanceRow(
                    sync_id=sync.id,
                    organization_id=org_id,
                    site_url="https://example.com/",
                    dimension_type="query_page",
                    query=q,
                    page_url=page,
                    start_date=date(2026, 8, 1),
                    end_date=date(2026, 8, 28),
                    clicks=clicks,
                    impressions=imp,
                    ctr=clicks / imp,
                    average_position=pos,
                    row_key=f"r{i}",
                    metrics_delta={},
                )
            )
        await db.commit()
        await KeywordOpportunityService(db).analyze(organization_id=org_id, sync_id=sync.id)
        await db.commit()
        return org_id, sync.id


@pytest.mark.asyncio
async def test_analyze_idempotent():
    org_id, sync_id = await _seed_org_with_data()
    async with AsyncSessionLocal() as db:
        svc = TopicClusteringService(db)
        r1 = await svc.analyze(organization_id=org_id, sync_id=sync_id)
        await db.commit()
        count1 = await db.scalar(select(func.count()).select_from(TopicCluster).where(TopicCluster.sync_id == sync_id))
        r2 = await svc.analyze(organization_id=org_id, sync_id=sync_id)
        await db.commit()
        count2 = await db.scalar(select(func.count()).select_from(TopicCluster).where(TopicCluster.sync_id == sync_id))
    assert r1["topics_created"] == r2["topics_created"]
    assert count1 == count2
    assert r1["algorithm_version"] == ALGORITHM_VERSION


@pytest.mark.asyncio
async def test_tenant_isolation():
    org_id, sync_id = await _seed_org_with_data()
    other_org = uuid.uuid4()
    async with AsyncSessionLocal() as db:
        svc = TopicClusteringService(db)
        await svc.analyze(organization_id=org_id, sync_id=sync_id)
        await db.commit()
        topics = await svc.list_topics(organization_id=org_id, sync_id=sync_id)
        with pytest.raises(HTTPException):
            await svc.get_topic(organization_id=other_org, topic_id=topics[0].id)


@pytest.mark.asyncio
async def test_summary_counts():
    org_id, sync_id = await _seed_org_with_data()
    async with AsyncSessionLocal() as db:
        svc = TopicClusteringService(db)
        await svc.analyze(organization_id=org_id, sync_id=sync_id)
        await db.commit()
        summary = await svc.summary(organization_id=org_id, sync_id=sync_id)
    assert summary["total_topics"] >= 1
    assert summary["total_clustered_queries"] >= 1
    assert summary["algorithm_version"] == ALGORITHM_VERSION


@pytest.mark.asyncio
async def test_api_list_and_detail():
    org_id, sync_id = await _seed_org_with_data()
    email = f"t-{uuid.uuid4().hex[:8]}@example.com"
    async with AsyncSessionLocal() as db:
        user = User(email=email, hashed_password=hash_password("secret123"), full_name="Topic Tester")
        db.add(user)
        await db.flush()
        db.add(OrganizationMember(organization_id=org_id, user_id=user.id, role=MemberRole.owner))
        await db.commit()

    async with AsyncSessionLocal() as db:
        await TopicClusteringService(db).analyze(organization_id=org_id, sync_id=sync_id)
        await db.commit()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        login = await client.post("/api/v1/auth/login", json={"email": email, "password": "secret123"})
        token = login.json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}
        analyze = await client.post("/api/v1/seo/topics/analyze", headers=headers, json={})
        assert analyze.status_code == 200
        listing = await client.get("/api/v1/seo/topics?limit=10&sort=impressions", headers=headers)
        assert listing.status_code == 200
        topics = listing.json()
        assert len(topics) >= 1
        detail = await client.get(f"/api/v1/seo/topics/{topics[0]['id']}", headers=headers)
        assert detail.status_code == 200
        queries = await client.get(f"/api/v1/seo/topics/{topics[0]['id']}/queries", headers=headers)
        assert queries.status_code == 200
        pages = await client.get(f"/api/v1/seo/topics/{topics[0]['id']}/pages", headers=headers)
        assert pages.status_code == 200
        summary = await client.get("/api/v1/seo/topics/summary", headers=headers)
        assert summary.status_code == 200
