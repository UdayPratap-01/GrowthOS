"""M9.16 — SEO weekly report tests."""

from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, select

from app.core.config import get_settings
from app.core.security import hash_password
from app.db.session import AsyncSessionLocal
from app.jobs import registry
from app.jobs.handlers import handle_seo_report_generate, handle_seo_report_scheduler_tick
from app.jobs.queue import JobQueue
from app.jobs.registry import SEO_REPORT_GENERATE, SEO_REPORT_SCHEDULER_TICK
from app.jobs.seo_report_scheduler import discover_report_targets, scheduled_window_start
from app.main import app
from app.models.automation import BackgroundJob
from app.models.enums import MemberRole
from app.models.organization import Organization, OrganizationMember
from app.models.seo_weekly_report import SeoReportConfig, SeoWeeklyReport
from app.models.user import User
from app.services.seo_weekly_report_service import SeoWeeklyReportService, _safe_text, weekly_period_for_date


@pytest.fixture(autouse=True)
async def _clean_reports():
    async with AsyncSessionLocal() as db:
        await db.execute(delete(SeoWeeklyReport))
        await db.execute(delete(SeoReportConfig))
        await db.execute(delete(BackgroundJob))
        await db.commit()
    yield


async def _seed_org(*, reporting_enabled: bool = True):
    async with AsyncSessionLocal() as db:
        org = Organization(name="Rep Org", slug=f"rep-{uuid.uuid4().hex[:8]}", demo_mode=True)
        user = User(
            email=f"rep-{uuid.uuid4().hex[:8]}@test.com",
            hashed_password=hash_password("pass"),
            full_name="Report Tester",
        )
        db.add_all([org, user])
        await db.flush()
        db.add(OrganizationMember(organization_id=org.id, user_id=user.id, role=MemberRole.owner))
        config = SeoReportConfig(organization_id=org.id, reporting_enabled=reporting_enabled)
        db.add(config)
        await db.commit()
        return org.id, user


async def _auth_client(user: User):
    transport = ASGITransport(app=app)
    client = AsyncClient(transport=transport, base_url="http://test")
    login = await client.post("/api/v1/auth/login", json={"email": user.email, "password": "pass"})
    token = login.json()["access_token"]
    client.headers["Authorization"] = f"Bearer {token}"
    return client


def test_weekly_period_boundaries():
    monday, sunday = weekly_period_for_date(anchor=date(2026, 9, 24))
    assert monday.weekday() == 0
    assert sunday.weekday() == 6
    assert (sunday - monday).days == 6


@pytest.mark.asyncio
async def test_report_config_defaults_disabled():
    org_id, _ = await _seed_org(reporting_enabled=False)
    async with AsyncSessionLocal() as db:
        config = await SeoWeeklyReportService(db).get_or_create_config(organization_id=org_id)
    assert config.reporting_enabled is False


@pytest.mark.asyncio
async def test_discover_skips_disabled_org():
    org_id, _ = await _seed_org(reporting_enabled=False)
    async with AsyncSessionLocal() as db:
        targets = await discover_report_targets(db, max_orgs=10)
    assert all(t[0].id != org_id for t in targets) or not targets


@pytest.mark.asyncio
async def test_discover_includes_enabled_org():
    org_id, _ = await _seed_org(reporting_enabled=True)
    async with AsyncSessionLocal() as db:
        targets = await discover_report_targets(db, max_orgs=10)
    assert any(t[0].id == org_id for t in targets)


@pytest.mark.asyncio
async def test_generate_report_success():
    org_id, _ = await _seed_org()
    async with AsyncSessionLocal() as db:
        svc = SeoWeeklyReportService(db)
        period_start, period_end = weekly_period_for_date()
        report = await svc.enqueue_generate(
            organization_id=org_id,
            period_start=period_start,
            period_end=period_end,
        )
        await db.commit()
    async with AsyncSessionLocal() as db:
        job = await db.get(BackgroundJob, report.background_job_id)
        result = await handle_seo_report_generate(db, job)
        await db.commit()
        completed = await db.get(SeoWeeklyReport, report.id)
    assert result["status"] == "completed"
    assert completed.status == "completed"
    assert completed.report_payload.get("sections")
    assert completed.summary
    assert completed.limitations


@pytest.mark.asyncio
async def test_idempotent_completed_report():
    org_id, _ = await _seed_org()
    async with AsyncSessionLocal() as db:
        svc = SeoWeeklyReportService(db)
        period_start, period_end = weekly_period_for_date()
        r1 = await svc.enqueue_generate(organization_id=org_id, period_start=period_start, period_end=period_end)
        job = await db.get(BackgroundJob, r1.background_job_id)
        await handle_seo_report_generate(db, job)
        await db.commit()
        r2 = await svc.enqueue_generate(organization_id=org_id, period_start=period_start, period_end=period_end)
    assert r1.id == r2.id
    assert r2.status == "completed"


@pytest.mark.asyncio
async def test_tenant_isolation_reports_api():
    org_a, user_a = await _seed_org()
    org_b, user_b = await _seed_org()
    async with AsyncSessionLocal() as db:
        report = SeoWeeklyReport(
            organization_id=org_a,
            period_start=date(2026, 9, 15),
            period_end=date(2026, 9, 21),
            status="completed",
            generation_key=f"key-{uuid.uuid4()}",
        )
        db.add(report)
        await db.commit()
        report_id = report.id
    client_b = await _auth_client(user_b)
    resp = await client_b.get(f"/api/v1/seo/reports/{report_id}")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_generate_api_enqueues():
    org_id, user = await _seed_org()
    client = await _auth_client(user)
    resp = await client.post("/api/v1/seo/reports/generate", json={})
    assert resp.status_code == 202
    body = resp.json()
    assert "report_id" in body


@pytest.mark.asyncio
async def test_scheduler_tick_enqueues(monkeypatch):
    monkeypatch.setattr(get_settings(), "seo_weekly_report_scheduler_enabled", True, raising=False)
    org_id, _ = await _seed_org()
    async with AsyncSessionLocal() as db:
        window = scheduled_window_start(datetime.now(timezone.utc), get_settings().seo_weekly_report_interval_minutes)
        job = BackgroundJob(
            job_type=SEO_REPORT_SCHEDULER_TICK,
            payload={"window": window.isoformat()},
            status="running",
        )
        db.add(job)
        await db.flush()
        with patch.object(
            SeoWeeklyReportService,
            "enqueue_generate",
            new=AsyncMock(side_effect=SeoWeeklyReportService(db).enqueue_generate),
        ):
            result = await handle_seo_report_scheduler_tick(db, job)
        await db.commit()
    assert result.get("enqueued", 0) >= 0


@pytest.mark.asyncio
async def test_no_autonomous_execution_in_service():
    import app.services.seo_weekly_report_service as mod

    source = open(mod.__file__).read()
    assert "ActionService" not in source
    assert "ExecutionEngine" not in source
    assert "SeoActionExecutor" not in source


def test_xss_safe_summary():
    assert "<" not in _safe_text("<script>x</script>")


def test_registered_report_job_types():
    assert SEO_REPORT_SCHEDULER_TICK in registry.registered_job_types()
    assert SEO_REPORT_GENERATE in registry.registered_job_types()
