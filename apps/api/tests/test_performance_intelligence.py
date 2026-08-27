"""AI Performance Intelligence — analysis-only recommendations from MPD."""

from __future__ import annotations

import uuid
from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import AsyncMock

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select

from app.ai.providers.base import AIResponse
from app.analytics.confidence import score_confidence
from app.analytics.explain import deterministic_explanation, reject_hallucinated_metrics
from app.analytics.intelligence import (
    PerformanceIntelligenceService,
    analytics_analyze_dedupe_key,
)
from app.analytics.metrics import compute_derived_metrics
from app.analytics.signals import detect_signals
from app.analytics.windows import (
    AnalysisWindow,
    EntityPeriodComparison,
    PeriodTotals,
    pct_change,
)
from app.core.config import get_settings
from app.core.security import hash_password
from app.db.session import AsyncSessionLocal
from app.jobs import handlers
from app.jobs.queue import JobQueue
from app.jobs.registry import ANALYTICS_ANALYZE
from app.main import app
from app.models.ai_ops import AuditLog
from app.models.automation import BackgroundJob
from app.models.client import Client
from app.models.enums import DataSource, MemberRole, PerformanceRecommendationStatus
from app.models.marketing import MarketingPerformanceDaily
from app.models.organization import Organization, OrganizationMember
from app.models.performance_intelligence import PerformanceRecommendation
from app.models.user import User


async def _seed_org(*, prefix: str = "intel"):
    async with AsyncSessionLocal() as db:
        org = Organization(
            name=f"Intel Org {uuid.uuid4().hex[:6]}",
            slug=f"{prefix}-{uuid.uuid4().hex[:8]}",
            demo_mode=False,
        )
        user = User(
            email=f"{prefix}-{uuid.uuid4().hex[:8]}@test.com",
            hashed_password=hash_password("pass"),
            full_name="Intel Tester",
        )
        db.add_all([org, user])
        await db.flush()
        db.add(OrganizationMember(organization_id=org.id, user_id=user.id, role=MemberRole.owner))
        client = Client(organization_id=org.id, business_name="Intel Client", industry="saas")
        db.add(client)
        await db.commit()
        return org.id, client.id, user.id


def _low_thresholds(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "performance_min_spend", 10.0)
    monkeypatch.setattr(settings, "performance_min_impressions", 100)
    monkeypatch.setattr(settings, "performance_min_clicks", 5)
    monkeypatch.setattr(settings, "performance_min_conversions", 1.0)
    monkeypatch.setattr(settings, "performance_significant_change_percent", 20.0)
    monkeypatch.setattr(settings, "performance_sudden_change_percent", 50.0)
    monkeypatch.setattr(settings, "performance_min_days_with_data", 2)
    return settings


def _comparison(
    *,
    current: PeriodTotals,
    previous: PeriodTotals,
    days: int = 7,
    platform: str = "meta",
    campaign_id: str = "c1",
    org_id=None,
    client_id=None,
) -> EntityPeriodComparison:
    window = AnalysisWindow.for_days(days)
    cur = current.as_dict()
    prev = previous.as_dict()
    changes = {m: pct_change(cur.get(m), prev.get(m)) for m in (
        "impressions", "clicks", "spend", "conversions", "leads", "revenue", "reach",
        "ctr", "cpc", "cpm", "cpl", "cpa", "roas",
    )}
    return EntityPeriodComparison(
        organization_id=org_id or uuid.uuid4(),
        client_id=client_id,
        platform=platform,
        entity_level="campaign",
        external_account_id="act",
        external_campaign_id=campaign_id,
        external_ad_set_id="",
        external_ad_id="",
        window=window,
        current=current,
        previous=previous,
        percentage_changes=changes,
    )


def test_metric_calculations_and_zero_denominators():
    m = compute_derived_metrics(
        impressions=2000, clicks=100, spend=200, conversions=10, leads=8, revenue=800
    )
    assert m.ctr == Decimal("5.000000")
    assert m.cpc == Decimal("2.000000")
    assert m.cpm == Decimal("100.000000")
    assert m.cpl == Decimal("25.000000")
    assert m.cpa == Decimal("20.000000")
    assert m.roas == Decimal("4.000000")
    z = compute_derived_metrics(impressions=0, clicks=0, spend=0, conversions=0, leads=0, revenue=0)
    assert z.ctr is z.cpc is z.cpm is z.cpl is z.cpa is z.roas is None


def test_windows_7_14_30():
    for days in (7, 14, 30):
        w = AnalysisWindow.for_days(days, as_of=date(2026, 8, 27))
        assert (w.current_end - w.current_start).days == days - 1
        assert (w.previous_end - w.previous_start).days == days - 1
        assert w.previous_end == w.current_start - timedelta(days=1)


def test_signal_worsening_cpl(monkeypatch):
    settings = _low_thresholds(monkeypatch)
    current = PeriodTotals(impressions=5000, clicks=200, spend=Decimal("800"), conversions=Decimal("10"), leads=10, days_with_data=7, row_count=7)
    previous = PeriodTotals(impressions=5000, clicks=200, spend=Decimal("500"), conversions=Decimal("10"), leads=10, days_with_data=7, row_count=7)
    # current cpl=80, previous cpl=50 → +60%
    signals = detect_signals(_comparison(current=current, previous=previous), settings=settings)
    types = {s.recommendation_type for s in signals}
    assert "REDUCE_BUDGET" in types
    assert any(s.metric == "cpl" for s in signals)


def test_signal_worsening_roas_and_ctr(monkeypatch):
    settings = _low_thresholds(monkeypatch)
    current = PeriodTotals(impressions=5000, clicks=50, spend=Decimal("200"), conversions=Decimal("2"), leads=2, revenue=Decimal("100"), days_with_data=7, row_count=7)
    previous = PeriodTotals(impressions=5000, clicks=200, spend=Decimal("200"), conversions=Decimal("10"), leads=10, revenue=Decimal("800"), days_with_data=7, row_count=7)
    signals = detect_signals(_comparison(current=current, previous=previous), settings=settings)
    metrics = {s.metric for s in signals}
    assert "roas" in metrics
    assert "ctr" in metrics


def test_signal_improving_roas_and_cpl(monkeypatch):
    settings = _low_thresholds(monkeypatch)
    current = PeriodTotals(impressions=5000, clicks=250, spend=Decimal("200"), conversions=Decimal("20"), leads=20, revenue=Decimal("1000"), days_with_data=7, row_count=7)
    previous = PeriodTotals(impressions=5000, clicks=100, spend=Decimal("200"), conversions=Decimal("5"), leads=5, revenue=Decimal("200"), days_with_data=7, row_count=7)
    signals = detect_signals(_comparison(current=current, previous=previous), settings=settings)
    cats = {s.category for s in signals}
    assert "POSITIVE" in cats


def test_signal_efficiency_high_spend_poor_cpl(monkeypatch):
    settings = _low_thresholds(monkeypatch)
    current = PeriodTotals(impressions=8000, clicks=300, spend=Decimal("900"), conversions=Decimal("5"), leads=5, days_with_data=7, row_count=7)
    previous = PeriodTotals(impressions=8000, clicks=300, spend=Decimal("900"), conversions=Decimal("5"), leads=5, days_with_data=7, row_count=7)
    signals = detect_signals(
        _comparison(current=current, previous=previous),
        settings=settings,
        account_avg_cpl=50.0,
    )
    assert any(s.category == "EFFICIENCY" and s.metric == "cpl" for s in signals)


def test_signal_low_spend_high_roas(monkeypatch):
    settings = _low_thresholds(monkeypatch)
    current = PeriodTotals(impressions=2000, clicks=80, spend=Decimal("15"), conversions=Decimal("5"), leads=5, revenue=Decimal("300"), days_with_data=7, row_count=7)
    previous = PeriodTotals(impressions=2000, clicks=80, spend=Decimal("15"), conversions=Decimal("5"), leads=5, revenue=Decimal("300"), days_with_data=7, row_count=7)
    signals = detect_signals(
        _comparison(current=current, previous=previous),
        settings=settings,
        account_avg_roas=2.0,
    )
    assert any(s.category == "EFFICIENCY" and "SCALE" in s.recommendation_type for s in signals)


def test_sudden_change_trend(monkeypatch):
    settings = _low_thresholds(monkeypatch)
    monkeypatch.setattr(settings, "performance_sudden_change_percent", 40.0)
    current = PeriodTotals(impressions=5000, clicks=200, spend=Decimal("100"), conversions=Decimal("2"), leads=2, days_with_data=7, row_count=7)
    previous = PeriodTotals(impressions=5000, clicks=200, spend=Decimal("100"), conversions=Decimal("10"), leads=10, days_with_data=7, row_count=7)
    signals = detect_signals(_comparison(current=current, previous=previous), settings=settings)
    assert any(s.category == "TREND" for s in signals)


def test_insufficient_sample_skips(monkeypatch):
    settings = _low_thresholds(monkeypatch)
    monkeypatch.setattr(settings, "performance_min_impressions", 10000)
    monkeypatch.setattr(settings, "performance_min_spend", 1000)
    current = PeriodTotals(impressions=10, clicks=1, spend=Decimal("1"), conversions=Decimal("0"), leads=0, days_with_data=1, row_count=1)
    previous = PeriodTotals(impressions=10, clicks=1, spend=Decimal("1"), conversions=Decimal("0"), leads=0, days_with_data=1, row_count=1)
    comparison = _comparison(current=current, previous=previous)
    signals = detect_signals(comparison, settings=settings)
    assert signals == []
    assert comparison.insufficient_data is True


def test_confidence_increases_with_volume(monkeypatch):
    settings = _low_thresholds(monkeypatch)
    weak = _comparison(
        current=PeriodTotals(impressions=120, clicks=6, spend=Decimal("12"), conversions=Decimal("1"), leads=1, days_with_data=2, row_count=2),
        previous=PeriodTotals(impressions=120, clicks=6, spend=Decimal("12"), conversions=Decimal("1"), leads=1, days_with_data=2, row_count=2),
    )
    strong = _comparison(
        current=PeriodTotals(impressions=20000, clicks=800, spend=Decimal("2000"), conversions=Decimal("80"), leads=80, days_with_data=7, row_count=7),
        previous=PeriodTotals(impressions=20000, clicks=800, spend=Decimal("2000"), conversions=Decimal("80"), leads=80, days_with_data=7, row_count=7),
    )
    from app.analytics.signals import PerformanceSignal

    sig = PerformanceSignal(
        category="UNDERPERFORMANCE",
        recommendation_type="REDUCE_BUDGET",
        severity="HIGH",
        title="x",
        metric="cpl",
        current=100,
        previous=50,
        change_percent=100,
        suggested_action={},
        evidence=[],
    )
    assert score_confidence(strong, sig, settings=settings) > score_confidence(weak, sig, settings=settings)


def test_hallucination_rejection():
    comparison = _comparison(
        current=PeriodTotals(impressions=1000, clicks=50, spend=Decimal("100"), conversions=Decimal("5"), leads=5, days_with_data=7, row_count=7),
        previous=PeriodTotals(impressions=1000, clicks=50, spend=Decimal("80"), conversions=Decimal("5"), leads=5, days_with_data=7, row_count=7),
    )
    from app.analytics.signals import PerformanceSignal

    sig = PerformanceSignal(
        category="UNDERPERFORMANCE",
        recommendation_type="REDUCE_BUDGET",
        severity="HIGH",
        title="CPL up",
        metric="cpl",
        current=20,
        previous=16,
        change_percent=25,
        suggested_action={"operation": "UPDATE_BUDGET", "direction": "DECREASE", "percentage": 15},
        evidence=[{"metric": "cpl", "current": 20, "previous": 16, "change_percent": 25}],
    )
    assert reject_hallucinated_metrics("CPL rose from 16 to 20 (+25%).", sig, comparison) is False
    assert reject_hallucinated_metrics("ROAS collapsed to 9999 overnight.", sig, comparison) is True
    text = deterministic_explanation(comparison, sig)
    assert "informational" in text.lower()


@pytest.mark.asyncio
async def test_analyze_persists_and_is_idempotent(monkeypatch):
    settings = _low_thresholds(monkeypatch)
    org_id, client_id, _ = await _seed_org()
    window = AnalysisWindow.for_days(7)

    async def seed_period(start: date, end: date, *, spend: str, leads: int, campaign: str = "camp-a"):
        async with AsyncSessionLocal() as db:
            day = start
            while day <= end:
                db.add(
                    MarketingPerformanceDaily(
                        organization_id=org_id,
                        client_id=client_id,
                        platform="meta",
                        entity_level="campaign",
                        external_account_id="act",
                        external_campaign_id=campaign,
                        date=day,
                        granularity="daily",
                        impressions=1000,
                        clicks=50,
                        spend=Decimal(spend),
                        conversions=Decimal(leads),
                        leads=leads,
                        revenue=Decimal("0"),
                        data_source=DataSource.live,
                    )
                )
                day += timedelta(days=1)
            await db.commit()

    # Previous cheaper CPL, current expensive CPL
    await seed_period(window.previous_start, window.previous_end, spend="10", leads=2)
    await seed_period(window.current_start, window.current_end, spend="40", leads=2)

    async with AsyncSessionLocal() as db:
        first = await PerformanceIntelligenceService(db).analyze(
            organization_id=org_id,
            client_id=client_id,
            window_days=7,
            use_ai_explanation=False,
            trigger="test",
        )
        await db.commit()
        second = await PerformanceIntelligenceService(db).analyze(
            organization_id=org_id,
            client_id=client_id,
            window_days=7,
            use_ai_explanation=False,
            trigger="test",
        )
        await db.commit()
        count = await db.scalar(
            select(func.count()).select_from(PerformanceRecommendation).where(
                PerformanceRecommendation.organization_id == org_id
            )
        )
        started = await db.scalar(
            select(AuditLog).where(
                AuditLog.organization_id == org_id,
                AuditLog.action == "analytics.analysis.started",
            )
        )
        created = await db.scalar(
            select(AuditLog).where(
                AuditLog.organization_id == org_id,
                AuditLog.action == "analytics.recommendation.created",
            )
        )
    assert first["success"] and second["success"]
    assert first["recommendations_created"] >= 1
    assert second["recommendations_created"] == 0
    assert count >= 1
    assert started is not None and created is not None
    assert settings.performance_min_spend == 10.0


@pytest.mark.asyncio
async def test_tenant_isolation_recommendations():
    org_a, client_a, _ = await _seed_org(prefix="ta")
    org_b, client_b, _ = await _seed_org(prefix="tb")
    async with AsyncSessionLocal() as db:
        db.add(
            PerformanceRecommendation(
                organization_id=org_a,
                client_id=client_a,
                platform="meta",
                entity_level="campaign",
                external_campaign_id="a",
                recommendation_type="REDUCE_BUDGET",
                severity="HIGH",
                title="A",
                explanation="A",
                evidence=[],
                affected_metrics=["cpl"],
                current_values={},
                comparison_values={},
                percentage_changes={},
                confidence=Decimal("0.5"),
                suggested_action={"informational_only": True},
                signal_category="UNDERPERFORMANCE",
                analysis_window_days=7,
                window_current_start=date.today() - timedelta(days=6),
                window_current_end=date.today(),
                window_previous_start=date.today() - timedelta(days=13),
                window_previous_end=date.today() - timedelta(days=7),
                fingerprint=f"fp-a-{uuid.uuid4().hex[:8]}",
                status=PerformanceRecommendationStatus.new,
            )
        )
        await db.commit()
        rows_b, total_b = await PerformanceIntelligenceService(db).list_recommendations(organization_id=org_b)
        leaked = await PerformanceIntelligenceService(db).get_recommendation(
            organization_id=org_b,
            recommendation_id=(
                await db.scalar(select(PerformanceRecommendation.id).where(PerformanceRecommendation.organization_id == org_a))
            ),
        )
        rows_wrong_client, total_wrong = await PerformanceIntelligenceService(db).list_recommendations(
            organization_id=org_a, client_id=client_b
        )
    assert total_b == 0 and rows_b == []
    assert leaked is None
    assert total_wrong == 0 and rows_wrong_client == []


@pytest.mark.asyncio
async def test_lifecycle_approved_does_not_execute(monkeypatch):
    org_id, client_id, user_id = await _seed_org(prefix="life")
    async with AsyncSessionLocal() as db:
        row = PerformanceRecommendation(
            organization_id=org_id,
            client_id=client_id,
            platform="google_ads",
            entity_level="campaign",
            external_campaign_id="g1",
            recommendation_type="SCALE_BUDGET",
            severity="MEDIUM",
            title="Scale",
            explanation="informational",
            evidence=[],
            affected_metrics=["roas"],
            current_values={},
            comparison_values={},
            percentage_changes={},
            confidence=Decimal("0.7"),
            suggested_action={"operation": "UPDATE_BUDGET", "informational_only": True, "execution_disabled": True},
            signal_category="POSITIVE",
            analysis_window_days=7,
            window_current_start=date.today() - timedelta(days=6),
            window_current_end=date.today(),
            window_previous_start=date.today() - timedelta(days=13),
            window_previous_end=date.today() - timedelta(days=7),
            fingerprint=f"fp-life-{uuid.uuid4().hex[:8]}",
            status=PerformanceRecommendationStatus.new,
        )
        db.add(row)
        await db.commit()
        rec_id = row.id
        updated = await PerformanceIntelligenceService(db).update_status(
            organization_id=org_id,
            recommendation_id=rec_id,
            status=PerformanceRecommendationStatus.approved,
            actor_user_id=user_id,
        )
        await db.commit()
        from app.models.automation import AIAction

        actions = await db.scalar(
            select(func.count()).select_from(AIAction).where(AIAction.organization_id == org_id)
        )
    assert updated is not None
    assert updated.status == PerformanceRecommendationStatus.approved
    assert updated.suggested_action.get("execution_disabled") is True
    assert actions == 0


@pytest.mark.asyncio
async def test_ai_fallback_and_malformed(monkeypatch):
    org_id, client_id, _ = await _seed_org(prefix="ai")
    settings = _low_thresholds(monkeypatch)
    window = AnalysisWindow.for_days(7)

    async with AsyncSessionLocal() as db:
        for i in range(7):
            day_c = window.current_start + timedelta(days=i)
            day_p = window.previous_start + timedelta(days=i)
            for day, spend in ((day_c, "40"), (day_p, "10")):
                db.add(
                    MarketingPerformanceDaily(
                        organization_id=org_id,
                        client_id=client_id,
                        platform="meta",
                        entity_level="campaign",
                        external_account_id="act",
                        external_campaign_id="ai-camp",
                        date=day,
                        granularity="daily",
                        impressions=1000,
                        clicks=40,
                        spend=Decimal(spend),
                        conversions=Decimal("2"),
                        leads=2,
                        revenue=Decimal("0"),
                        data_source=DataSource.live,
                    )
                )
        await db.commit()

    class Boom:
        async def complete(self, *args, **kwargs):
            raise RuntimeError("provider down")

    monkeypatch.setattr("app.analytics.explain.get_ai_provider", lambda: Boom())
    async with AsyncSessionLocal() as db:
        result = await PerformanceIntelligenceService(db).analyze(
            organization_id=org_id,
            client_id=client_id,
            window_days=7,
            use_ai_explanation=True,
        )
        await db.commit()
        row = await db.scalar(
            select(PerformanceRecommendation).where(PerformanceRecommendation.organization_id == org_id)
        )
    assert result["success"]
    assert row is not None
    assert row.explanation_source == "deterministic"
    assert "informational" in row.explanation.lower()

    class Hallucinator:
        async def complete(self, *args, **kwargs):
            return AIResponse(content="Spend exploded to 777777 dollars mysteriously.", provider="mock")

    monkeypatch.setattr("app.analytics.explain.get_ai_provider", lambda: Hallucinator())
    async with AsyncSessionLocal() as db:
        await PerformanceIntelligenceService(db).analyze(
            organization_id=org_id,
            client_id=client_id,
            window_days=7,
            use_ai_explanation=True,
        )
        await db.commit()
        row = await db.scalar(
            select(PerformanceRecommendation).where(PerformanceRecommendation.organization_id == org_id)
        )
    assert row.explanation_source == "deterministic"
    assert settings.performance_min_clicks == 5


@pytest.mark.asyncio
async def test_worker_analyze_job(monkeypatch):
    org_id, client_id, user_id = await _seed_org(prefix="job")
    monkeypatch.setattr(
        PerformanceIntelligenceService,
        "analyze",
        AsyncMock(return_value={"success": True, "recommendations_created": 1}),
    )
    async with AsyncSessionLocal() as db:
        job = await JobQueue(db).enqueue(
            job_type=ANALYTICS_ANALYZE,
            payload={
                "client_id": str(client_id),
                "window_days": 7,
                "actor_user_id": str(user_id),
                "use_ai_explanation": False,
            },
            organization_id=org_id,
            dedupe_key=analytics_analyze_dedupe_key(
                organization_id=org_id, client_id=client_id, window_days=7, platform=None
            ),
        )
        second = await JobQueue(db).enqueue(
            job_type=ANALYTICS_ANALYZE,
            payload={"client_id": str(client_id), "window_days": 7},
            organization_id=org_id,
            dedupe_key=analytics_analyze_dedupe_key(
                organization_id=org_id, client_id=client_id, window_days=7, platform=None
            ),
        )
        await db.commit()
        assert job.id == second.id
        result = await handlers.handle_analytics_analyze(db, await db.get(BackgroundJob, job.id))
    assert result["success"] is True


@pytest.mark.asyncio
async def test_api_auth_and_list():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        denied = await client.get("/api/v1/analytics/recommendations")
        assert denied.status_code in {401, 403}
        login = await client.post(
            "/api/v1/auth/login", json={"email": "demo@growthos.ai", "password": "demo1234"}
        )
        assert login.status_code == 200
        headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
        resp = await client.get("/api/v1/analytics/recommendations", headers=headers)
        assert resp.status_code == 200
        body = resp.json()
        assert "items" in body and "total" in body


@pytest.mark.asyncio
async def test_no_performance_data_analyze():
    org_id, client_id, _ = await _seed_org(prefix="empty")
    async with AsyncSessionLocal() as db:
        result = await PerformanceIntelligenceService(db).analyze(
            organization_id=org_id,
            client_id=client_id,
            window_days=7,
            use_ai_explanation=False,
        )
        await db.commit()
    assert result["success"] is True
    assert result["entities_analyzed"] == 0
    assert result["recommendations_created"] == 0
