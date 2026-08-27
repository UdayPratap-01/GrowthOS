"""Analytics ingestion foundation — comprehensive coverage."""

from __future__ import annotations

import uuid
from datetime import date, timedelta
from decimal import Decimal

import httpx
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select

from app.analytics.errors import (
    CredentialsMissing,
    IntegrationDisconnected,
    ProviderRateLimited,
    ProviderTimeout,
    ProviderTransportError,
)
from app.analytics.ingestion import AnalyticsIngestionService, analytics_ingest_dedupe_key
from app.analytics.metrics import compute_derived_metrics
from app.analytics.normalize import NormalizedPerformanceRow
from app.analytics.providers.google_ads import normalize_google_ads_row
from app.analytics.providers.meta import MetaInsightsFetcher, normalize_meta_insight_row
from app.core.security import hash_password
from app.db.session import AsyncSessionLocal
from app.jobs import handlers
from app.jobs.queue import JobQueue
from app.jobs.registry import ANALYTICS_INGEST
from app.main import app
from app.models.ai_ops import AuditLog, Integration
from app.models.automation import BackgroundJob
from app.models.client import Client
from app.models.enums import MemberRole
from app.models.marketing import MarketingPerformanceDaily
from app.models.organization import Organization, OrganizationMember
from app.models.user import User


async def _seed_org(*, email_prefix: str = "ingest"):
    async with AsyncSessionLocal() as db:
        org = Organization(
            name=f"Ingest Org {uuid.uuid4().hex[:6]}",
            slug=f"ingest-{uuid.uuid4().hex[:8]}",
            demo_mode=False,
        )
        user = User(
            email=f"{email_prefix}-{uuid.uuid4().hex[:8]}@test.com",
            hashed_password=hash_password("pass"),
            full_name="Ingest Tester",
        )
        db.add_all([org, user])
        await db.flush()
        db.add(OrganizationMember(organization_id=org.id, user_id=user.id, role=MemberRole.owner))
        client = Client(organization_id=org.id, business_name="Ingest Client", industry="saas")
        db.add(client)
        await db.commit()
        return org.id, client.id, user.id, user.email


def test_derived_metrics_happy_path():
    m = compute_derived_metrics(
        impressions=1000,
        clicks=50,
        spend=Decimal("100"),
        conversions=Decimal("10"),
        leads=5,
        revenue=Decimal("400"),
    )
    assert m.ctr == Decimal("5.000000")
    assert m.cpc == Decimal("2.000000")
    assert m.cpm == Decimal("100.000000")
    assert m.cpl == Decimal("20.000000")
    assert m.cpa == Decimal("10.000000")
    assert m.roas == Decimal("4.000000")


def test_derived_metrics_zero_denominators():
    m = compute_derived_metrics(
        impressions=0,
        clicks=0,
        spend=0,
        conversions=0,
        leads=0,
        revenue=0,
    )
    assert m.ctr is None
    assert m.cpc is None
    assert m.cpm is None
    assert m.cpl is None
    assert m.cpa is None
    assert m.roas is None


def test_meta_normalization():
    org_id = uuid.uuid4()
    row = normalize_meta_insight_row(
        organization_id=org_id,
        client_id=None,
        account_id="act_1",
        row={
            "campaign_id": "111",
            "campaign_name": "Test",
            "date_start": "2026-08-01",
            "impressions": "100",
            "reach": "80",
            "clicks": "10",
            "spend": "25.50",
            "actions": [{"action_type": "lead", "value": "2"}],
            "action_values": [{"action_type": "purchase", "value": "90"}],
            "access_token": "should-be-stripped-from-metadata-path",
        },
        entity_level="campaign",
    )
    assert row is not None
    assert row.platform == "meta"
    assert row.external_campaign_id == "111"
    assert row.leads == 2
    assert row.spend == Decimal("25.50")
    assert row.reach == 80
    assert "access_token" not in row.provider_metadata


def test_meta_malformed_row_returns_none():
    assert (
        normalize_meta_insight_row(
            organization_id=uuid.uuid4(),
            client_id=None,
            account_id="act_1",
            row={"impressions": "nope"},
            entity_level="campaign",
        )
        is None
    )


def test_google_normalization():
    org_id = uuid.uuid4()
    row = normalize_google_ads_row(
        organization_id=org_id,
        client_id=None,
        customer_id="123",
        item={
            "campaign": {"id": "999", "name": "G", "status": "ENABLED"},
            "metrics": {
                "impressions": "200",
                "clicks": "20",
                "costMicros": "5000000",
                "conversions": "3.5",
                "conversionsValue": "70",
            },
            "segments": {"date": "2026-08-02"},
        },
    )
    assert row is not None
    assert row.platform == "google_ads"
    assert row.spend == Decimal("5.0000")
    assert row.conversions == Decimal("3.5")
    assert row.revenue == Decimal("70")


@pytest.mark.asyncio
async def test_idempotent_upsert():
    org_id, client_id, _, _ = await _seed_org()
    day = date.today() - timedelta(days=1)
    row = NormalizedPerformanceRow(
        organization_id=org_id,
        client_id=client_id,
        platform="meta",
        entity_level="campaign",
        date=day,
        external_account_id="act_1",
        external_campaign_id="c1",
        impressions=10,
        clicks=1,
        spend=Decimal("5"),
        conversions=Decimal("1"),
        leads=1,
        revenue=Decimal("0"),
    )
    async with AsyncSessionLocal() as db:
        service = AnalyticsIngestionService(db)
        n1 = await service.upsert_rows([row])
        row.impressions = 50
        row.spend = Decimal("12")
        n2 = await service.upsert_rows([row])
        await db.commit()
        count = await db.scalar(
            select(func.count()).select_from(MarketingPerformanceDaily).where(
                MarketingPerformanceDaily.organization_id == org_id
            )
        )
        stored = await db.scalar(
            select(MarketingPerformanceDaily).where(
                MarketingPerformanceDaily.organization_id == org_id,
                MarketingPerformanceDaily.external_campaign_id == "c1",
                MarketingPerformanceDaily.date == day,
            )
        )
    assert n1 == 1 and n2 == 1
    assert count == 1
    assert stored.impressions == 50
    assert stored.spend == Decimal("12")
    assert stored.ctr is not None
    assert "access_token" not in (stored.provider_metadata or {})


@pytest.mark.asyncio
async def test_tenant_isolation_list_and_ingest():
    org_a, client_a, _, _ = await _seed_org(email_prefix="a")
    org_b, client_b, _, _ = await _seed_org(email_prefix="b")
    day = date.today()
    async with AsyncSessionLocal() as db:
        service = AnalyticsIngestionService(db)
        await service.upsert_rows(
            [
                NormalizedPerformanceRow(
                    organization_id=org_a,
                    client_id=client_a,
                    platform="meta",
                    entity_level="campaign",
                    date=day,
                    external_account_id="a",
                    external_campaign_id="ca",
                    impressions=10,
                    clicks=1,
                    spend=Decimal("1"),
                ),
                NormalizedPerformanceRow(
                    organization_id=org_b,
                    client_id=client_b,
                    platform="meta",
                    entity_level="campaign",
                    date=day,
                    external_account_id="b",
                    external_campaign_id="cb",
                    impressions=99,
                    clicks=9,
                    spend=Decimal("9"),
                ),
            ]
        )
        await db.commit()
        rows_a, total_a = await service.list_performance(organization_id=org_a)
        rows_b, total_b = await service.list_performance(organization_id=org_b)
        # Foreign client under wrong org returns empty
        rows_x, total_x = await service.list_performance(organization_id=org_a, client_id=client_b)
    assert total_a == 1 and rows_a[0].external_campaign_id == "ca"
    assert total_b == 1 and rows_b[0].external_campaign_id == "cb"
    assert total_x == 0 and rows_x == []


@pytest.mark.asyncio
async def test_date_range_and_pagination():
    org_id, client_id, _, _ = await _seed_org()
    async with AsyncSessionLocal() as db:
        service = AnalyticsIngestionService(db)
        rows = [
            NormalizedPerformanceRow(
                organization_id=org_id,
                client_id=client_id,
                platform="google_ads",
                entity_level="campaign",
                date=date.today() - timedelta(days=i),
                external_account_id="cust",
                external_campaign_id=f"c{i}",
                impressions=i + 1,
                clicks=1,
                spend=Decimal("1"),
            )
            for i in range(5)
        ]
        await service.upsert_rows(rows)
        await db.commit()
        page1, total = await service.list_performance(
            organization_id=org_id,
            date_from=date.today() - timedelta(days=3),
            date_to=date.today(),
            limit=2,
            offset=0,
        )
        page2, _ = await service.list_performance(
            organization_id=org_id,
            date_from=date.today() - timedelta(days=3),
            date_to=date.today(),
            limit=2,
            offset=2,
        )
    assert total == 4
    assert len(page1) == 2
    assert len(page2) == 2
    assert {r.external_campaign_id for r in page1}.isdisjoint({r.external_campaign_id for r in page2})


@pytest.mark.asyncio
async def test_missing_credentials_and_disconnected():
    org_id, client_id, _, _ = await _seed_org()
    async with AsyncSessionLocal() as db:
        service = AnalyticsIngestionService(db)
        with pytest.raises(IntegrationDisconnected):
            await service.ingest(
                organization_id=org_id,
                provider="meta",
                client_id=client_id,
                lookback_days=3,
            )
        failed = await db.scalar(
            select(AuditLog).where(
                AuditLog.organization_id == org_id,
                AuditLog.action == "analytics.ingestion_failed",
            )
        )
    assert failed is not None
    assert failed.details["error_code"] == "INTEGRATION_DISCONNECTED"


@pytest.mark.asyncio
async def test_provider_timeout_classified(monkeypatch):
    org_id, client_id, _, _ = await _seed_org()
    async with AsyncSessionLocal() as db:
        db.add(
            Integration(
                organization_id=org_id,
                client_id=client_id,
                provider="meta",
                status="connected",
                secret_ref="enc",
                config={},
            )
        )
        await db.commit()

    monkeypatch.setattr(
        "app.analytics.providers.meta.load_tokens",
        lambda row: {"access_token": "tok"},
    )

    async def boom(*args, **kwargs):
        raise httpx.TimeoutException("timed out")

    monkeypatch.setattr(httpx.AsyncClient, "get", boom)

    async with AsyncSessionLocal() as db:
        with pytest.raises(ProviderTimeout):
            await MetaInsightsFetcher(db).fetch(
                organization_id=org_id,
                client_id=client_id,
                lookback_days=3,
            )


@pytest.mark.asyncio
async def test_provider_transport_and_rate_limit(monkeypatch):
    org_id, client_id, _, _ = await _seed_org()
    async with AsyncSessionLocal() as db:
        db.add(
            Integration(
                organization_id=org_id,
                client_id=client_id,
                provider="meta",
                status="connected",
                secret_ref="enc",
                config={},
            )
        )
        await db.commit()

    monkeypatch.setattr(
        "app.analytics.providers.meta.load_tokens",
        lambda row: {"access_token": "tok"},
    )

    class RateResp:
        status_code = 429
        content = b"{}"

        def json(self):
            return {}

    async def rate_limited(*args, **kwargs):
        return RateResp()

    monkeypatch.setattr(httpx.AsyncClient, "get", rate_limited)
    async with AsyncSessionLocal() as db:
        with pytest.raises(ProviderRateLimited):
            await MetaInsightsFetcher(db).fetch(
                organization_id=org_id, client_id=client_id, lookback_days=1
            )

    async def transport_fail(*args, **kwargs):
        raise httpx.ConnectError("reset")

    monkeypatch.setattr(httpx.AsyncClient, "get", transport_fail)
    async with AsyncSessionLocal() as db:
        with pytest.raises(ProviderTransportError):
            await MetaInsightsFetcher(db).fetch(
                organization_id=org_id, client_id=client_id, lookback_days=1
            )


@pytest.mark.asyncio
async def test_sanitized_provider_metadata_persisted():
    org_id, client_id, _, _ = await _seed_org()
    row = NormalizedPerformanceRow(
        organization_id=org_id,
        client_id=client_id,
        platform="meta",
        entity_level="campaign",
        date=date.today(),
        external_account_id="act",
        external_campaign_id="c",
        impressions=1,
        clicks=1,
        spend=Decimal("1"),
        provider_metadata={"access_token": "secret", "campaign_name": "ok", "Authorization": "Bearer x"},
    )
    async with AsyncSessionLocal() as db:
        await AnalyticsIngestionService(db).upsert_rows([row])
        await db.commit()
        stored = await db.scalar(
            select(MarketingPerformanceDaily).where(
                MarketingPerformanceDaily.organization_id == org_id
            )
        )
    assert "access_token" not in stored.provider_metadata
    assert "Authorization" not in stored.provider_metadata
    assert stored.provider_metadata.get("campaign_name") == "ok"


@pytest.mark.asyncio
async def test_worker_ingestion_success(monkeypatch):
    org_id, client_id, user_id, _ = await _seed_org()
    day = date.today()

    async def fake_fetch(self, **kwargs):
        return [
            NormalizedPerformanceRow(
                organization_id=org_id,
                client_id=client_id,
                platform="meta",
                entity_level="campaign",
                date=day,
                external_account_id="act",
                external_campaign_id="wc",
                impressions=10,
                clicks=2,
                spend=Decimal("4"),
                conversions=Decimal("1"),
                leads=1,
                revenue=Decimal("8"),
            )
        ]

    monkeypatch.setattr(AnalyticsIngestionService, "_fetch_provider_rows", fake_fetch)

    async with AsyncSessionLocal() as db:
        job = await JobQueue(db).enqueue(
            job_type=ANALYTICS_INGEST,
            payload={
                "provider": "meta",
                "client_id": str(client_id),
                "lookback_days": 7,
                "entity_level": "campaign",
                "actor_user_id": str(user_id),
            },
            organization_id=org_id,
            dedupe_key=analytics_ingest_dedupe_key(
                organization_id=org_id, provider="meta", client_id=client_id, lookback_days=7
            ),
        )
        await db.commit()
        job_row = await db.get(BackgroundJob, job.id)
        result = await handlers.handle_analytics_ingest(db, job_row)
        await db.commit()
        started = await db.scalar(
            select(AuditLog).where(
                AuditLog.organization_id == org_id,
                AuditLog.action == "analytics.ingestion_started",
            )
        )
        completed = await db.scalar(
            select(AuditLog).where(
                AuditLog.organization_id == org_id,
                AuditLog.action == "analytics.ingestion_completed",
            )
        )
        count = await db.scalar(
            select(func.count()).select_from(MarketingPerformanceDaily).where(
                MarketingPerformanceDaily.organization_id == org_id
            )
        )
    assert result["success"] is True
    assert result["upserted"] == 1
    assert started is not None and completed is not None
    assert count == 1


@pytest.mark.asyncio
async def test_enqueue_idempotent_and_api_auth():
    org_id, client_id, _, email = await _seed_org(email_prefix="api")
    # Direct enqueue dedupe
    async with AsyncSessionLocal() as db:
        service = AnalyticsIngestionService(db)
        first = await service.enqueue(
            organization_id=org_id, provider="meta", client_id=client_id, lookback_days=7
        )
        second = await service.enqueue(
            organization_id=org_id, provider="meta", client_id=client_id, lookback_days=7
        )
        await db.commit()
        assert first.id == second.id

    # HTTP list requires auth; unauthorized blocked
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        denied = await client.get("/api/v1/analytics/performance")
        assert denied.status_code in {401, 403}

        # Seed some data then login as demo won't see this org — use service path already covered.
        # Ensure performance endpoint works for seeded demo org without crashing.
        login = await client.post(
            "/api/v1/auth/login", json={"email": "demo@growthos.ai", "password": "demo1234"}
        )
        assert login.status_code == 200
        headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
        resp = await client.get("/api/v1/analytics/performance", headers=headers)
        assert resp.status_code == 200
        body = resp.json()
        assert "items" in body and "total" in body


@pytest.mark.asyncio
async def test_retryable_error_propagates_from_handler(monkeypatch):
    org_id, client_id, _, _ = await _seed_org()

    async def boom(self, **kwargs):
        raise ProviderTimeout("timed out")

    monkeypatch.setattr(AnalyticsIngestionService, "ingest", boom)

    async with AsyncSessionLocal() as db:
        job = await JobQueue(db).enqueue(
            job_type=ANALYTICS_INGEST,
            payload={"provider": "meta", "client_id": str(client_id), "lookback_days": 3},
            organization_id=org_id,
        )
        await db.commit()
        job_row = await db.get(BackgroundJob, job.id)
        with pytest.raises(ProviderTimeout):
            await handlers.handle_analytics_ingest(db, job_row)


@pytest.mark.asyncio
async def test_unrecoverable_missing_creds(monkeypatch):
    org_id, client_id, _, _ = await _seed_org()

    async def boom(self, **kwargs):
        raise CredentialsMissing("no token")

    monkeypatch.setattr(AnalyticsIngestionService, "ingest", boom)

    async with AsyncSessionLocal() as db:
        job = await JobQueue(db).enqueue(
            job_type=ANALYTICS_INGEST,
            payload={"provider": "meta", "client_id": str(client_id)},
            organization_id=org_id,
        )
        await db.commit()
        job_row = await db.get(BackgroundJob, job.id)
        with pytest.raises(handlers.UnrecoverableJobError):
            await handlers.handle_analytics_ingest(db, job_row)


@pytest.mark.asyncio
async def test_google_unsupported_entity_level():
    org_id, client_id, _, _ = await _seed_org()
    async with AsyncSessionLocal() as db:
        db.add(
            Integration(
                organization_id=org_id,
                client_id=client_id,
                provider="google_ads",
                status="connected",
                secret_ref="enc",
                config={"customer_id": "123"},
            )
        )
        await db.commit()
        from app.analytics.errors import UnsupportedOperation
        from app.analytics.providers.google_ads import GoogleAdsInsightsFetcher

        with pytest.raises(UnsupportedOperation):
            await GoogleAdsInsightsFetcher(db).fetch(
                organization_id=org_id,
                client_id=client_id,
                lookback_days=3,
                entity_level="ad",
            )
