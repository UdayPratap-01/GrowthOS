"""M9.15 — SEO continuous monitoring tests."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, select

from app.core.config import get_settings
from app.core.security import hash_password
from app.db.session import AsyncSessionLocal
from app.jobs import registry
from app.jobs.handlers import handle_seo_monitor_cycle, handle_seo_monitor_scheduler_tick
from app.jobs.queue import JobQueue
from app.jobs.seo_monitor_scheduler import (
    SEO_MONITOR_CYCLE,
    SEO_MONITOR_SCHEDULER_TICK,
    discover_monitor_targets,
    enqueue_monitor_cycle,
    ensure_seo_monitor_tick,
    organization_has_inflight_monitor_cycle,
    scheduled_window_start,
)
from app.main import app
from app.models.automation import BackgroundJob
from app.models.enums import MemberRole, SeoCrawlStatus, SeoMonitoringAlertStatus, SeoMonitoringAlertType
from app.models.organization import Organization, OrganizationMember
from app.models.seo import SeoCrawl, SeoFinding
from app.models.seo_monitoring import SeoMonitoringAlert, SeoMonitoringConfig, SeoMonitoringRun
from app.models.user import User
from app.services.seo_monitoring_service import SeoMonitoringService, _safe_text


@pytest.fixture(autouse=True)
async def _clean_monitoring():
    async with AsyncSessionLocal() as db:
        await db.execute(delete(SeoMonitoringAlert))
        await db.execute(delete(SeoMonitoringRun))
        await db.execute(delete(SeoMonitoringConfig))
        await db.execute(delete(BackgroundJob))
        await db.commit()
    yield


async def _seed_org(*, monitoring_enabled: bool = True, site_url: str = "https://example.com/"):
    async with AsyncSessionLocal() as db:
        org = Organization(name="Mon Org", slug=f"mon-{uuid.uuid4().hex[:8]}", demo_mode=True)
        user = User(
            email=f"mon-{uuid.uuid4().hex[:8]}@test.com",
            hashed_password=hash_password("pass"),
            full_name="Monitor Tester",
        )
        db.add_all([org, user])
        await db.flush()
        db.add(OrganizationMember(organization_id=org.id, user_id=user.id, role=MemberRole.owner))
        config = SeoMonitoringConfig(
            organization_id=org.id,
            monitoring_enabled=monitoring_enabled,
            site_root_url=site_url,
            crawl_monitoring_enabled=True,
            search_console_monitoring_enabled=False,
            competitor_monitoring_enabled=False,
        )
        db.add(config)
        await db.commit()
        return org.id, user


async def _auth_client(org_id: uuid.UUID, user: User):
    transport = ASGITransport(app=app)
    client = AsyncClient(transport=transport, base_url="http://test")
    login = await client.post("/api/v1/auth/login", json={"email": user.email, "password": "pass"})
    token = login.json()["access_token"]
    client.headers["Authorization"] = f"Bearer {token}"
    return client


@pytest.mark.asyncio
async def test_monitoring_config_defaults_disabled():
    org_id, _ = await _seed_org(monitoring_enabled=False)
    async with AsyncSessionLocal() as db:
        config = await SeoMonitoringService(db).get_or_create_config(organization_id=org_id)
    assert config.monitoring_enabled is False


@pytest.mark.asyncio
async def test_discover_skips_disabled_org():
    org_id, _ = await _seed_org(monitoring_enabled=False)
    async with AsyncSessionLocal() as db:
        targets = await discover_monitor_targets(db, max_orgs=10)
    assert all(t[0].id != org_id for t in targets) or not targets


@pytest.mark.asyncio
async def test_discover_includes_enabled_org():
    org_id, _ = await _seed_org(monitoring_enabled=True)
    async with AsyncSessionLocal() as db:
        targets = await discover_monitor_targets(db, max_orgs=10)
    assert any(t[0].id == org_id for t in targets)


@pytest.mark.asyncio
async def test_duplicate_concurrent_monitor_cycle_prevented():
    org_id, _ = await _seed_org()
    async with AsyncSessionLocal() as db:
        org = await db.get(Organization, org_id)
        window = scheduled_window_start(datetime.now(timezone.utc), 1440)
        await JobQueue(db).enqueue(
            job_type=SEO_MONITOR_CYCLE,
            payload={"trigger": "scheduler"},
            organization_id=org_id,
            dedupe_key=f"inflight:{org_id}",
            max_attempts=3,
        )
        await db.commit()
        assert await organization_has_inflight_monitor_cycle(db, org_id)
        result = await enqueue_monitor_cycle(db, organization=org, window_start=window)
        assert result.skipped is True


@pytest.mark.asyncio
async def test_scheduler_tick_enqueues_enabled_orgs(monkeypatch):
    monkeypatch.setattr(get_settings(), "seo_monitor_scheduler_enabled", True, raising=False)
    monkeypatch.setattr(get_settings(), "seo_monitor_max_orgs_per_cycle", 10, raising=False)
    org_id, _ = await _seed_org()
    async with AsyncSessionLocal() as db:
        window = scheduled_window_start(datetime.now(timezone.utc), get_settings().seo_monitor_interval_minutes)
        job = BackgroundJob(
            job_type=SEO_MONITOR_SCHEDULER_TICK,
            payload={"window": window.isoformat()},
            status="running",
        )
        db.add(job)
        await db.flush()
        result = await handle_seo_monitor_scheduler_tick(db, job)
        await db.commit()
    assert result["enqueued"] >= 1


@pytest.mark.asyncio
async def test_monitor_cycle_schedules_crawl(monkeypatch):
    org_id, _ = await _seed_org()
    async with AsyncSessionLocal() as db:
        job = await JobQueue(db).enqueue(
            job_type=SEO_MONITOR_CYCLE,
            payload={"trigger": "scheduler"},
            organization_id=org_id,
            dedupe_key=f"cycle-{uuid.uuid4()}",
        )
        await db.commit()
    async with AsyncSessionLocal() as db:
        job = await db.get(BackgroundJob, job.id)
        with patch.object(SeoMonitoringService, "_run_search_console_sync", new=AsyncMock(return_value={"completed": False})):
            with patch.object(
                SeoMonitoringService,
                "_schedule_crawl",
                new=AsyncMock(return_value={"enqueued": True, "crawl_id": str(uuid.uuid4())}),
            ):
                result = await handle_seo_monitor_cycle(db, job)
                await db.commit()
    assert "run_id" in result or result.get("skipped")


@pytest.mark.asyncio
async def test_new_high_finding_creates_alert():
    org_id, _ = await _seed_org()
    async with AsyncSessionLocal() as db:
        from app.models.enums import SeoFindingSeverity, SeoFindingStatus

        prev = SeoCrawl(
            organization_id=org_id,
            root_url="https://example.com/",
            status=SeoCrawlStatus.completed,
            config={},
            stats={"analysis_status": "completed"},
            completed_at=datetime.now(timezone.utc),
        )
        curr = SeoCrawl(
            organization_id=org_id,
            root_url="https://example.com/",
            status=SeoCrawlStatus.completed,
            config={},
            stats={"analysis_status": "completed"},
            completed_at=datetime.now(timezone.utc),
        )
        db.add_all([prev, curr])
        await db.flush()
        db.add(
            SeoFinding(
                crawl_id=prev.id,
                organization_id=org_id,
                rule_id="SEO_OLD_RULE",
                category="title",
                severity=SeoFindingSeverity.low,
                status=SeoFindingStatus.open,
                dedupe_key=f"dk-prev-{uuid.uuid4().hex[:8]}",
                title="Old finding",
                description="Old",
                evidence={},
                url="https://example.com/old",
            )
        )
        db.add(
            SeoFinding(
                crawl_id=curr.id,
                organization_id=org_id,
                rule_id="SEO_TITLE_MISSING",
                category="title",
                severity=SeoFindingSeverity.high,
                status=SeoFindingStatus.open,
                dedupe_key=f"dk-{uuid.uuid4().hex[:8]}",
                title="Missing title",
                description="No title",
                evidence={},
                url="https://example.com/page",
            )
        )
        await db.commit()
        svc = SeoMonitoringService(db)
        out = await svc.process_crawl_completion(
            organization_id=org_id,
            crawl_id=curr.id,
        )
        await db.commit()
    assert out.get("alerts_generated", 0) >= 1


@pytest.mark.asyncio
async def test_alert_deduplication():
    from app.models.enums import SeoMonitoringAlertSeverity

    org_id, _ = await _seed_org()
    async with AsyncSessionLocal() as db:
        svc = SeoMonitoringService(db)
        created = await svc._create_alert(
            organization_id=org_id,
            alert_type=SeoMonitoringAlertType.monitoring_job_failed,
            severity=SeoMonitoringAlertSeverity.high,
            title="Test",
            summary="Test",
            dedupe_key=f"dedupe:{org_id}",
        )
        assert created is True
        duplicate = await svc._create_alert(
            organization_id=org_id,
            alert_type=SeoMonitoringAlertType.monitoring_job_failed,
            severity=SeoMonitoringAlertSeverity.high,
            title="Test again",
            summary="Test again",
            dedupe_key=f"dedupe:{org_id}",
        )
        assert duplicate is False
        alerts = (
            await db.execute(select(SeoMonitoringAlert).where(SeoMonitoringAlert.organization_id == org_id))
        ).scalars().all()
        assert len(alerts) == 1


@pytest.mark.asyncio
async def test_tenant_isolation_monitoring_api():
    org_a, user_a = await _seed_org()
    org_b, user_b = await _seed_org()
    async with AsyncSessionLocal() as db:
        run = SeoMonitoringRun(
            organization_id=org_a,
            run_type="aggregate",
            trigger="manual",
            status="completed",
        )
        db.add(run)
        await db.commit()
        run_id = run.id
    client_b = await _auth_client(org_b, user_b)
    resp = await client_b.get(f"/api/v1/seo/monitoring/runs/{run_id}")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_manual_run_respects_disabled():
    org_id, user = await _seed_org(monitoring_enabled=False)
    client = await _auth_client(org_id, user)
    resp = await client.post("/api/v1/seo/monitoring/run")
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_dashboard_includes_monitoring():
    org_id, user = await _seed_org()
    client = await _auth_client(org_id, user)
    resp = await client.get("/api/v1/seo/dashboard")
    assert resp.status_code == 200
    assert "monitoring" in resp.json()


@pytest.mark.asyncio
async def test_xss_safe_alert_text():
    malicious = "<script>alert(1)</script>"
    cleaned = _safe_text(malicious)
    assert "<" not in cleaned
    assert "script" in cleaned


@pytest.mark.asyncio
async def test_monitoring_does_not_execute_actions():
    """Monitoring cycle must not import or invoke SEO action execution."""
    org_id, _ = await _seed_org()
    async with AsyncSessionLocal() as db:
        job = await JobQueue(db).enqueue(
            job_type=SEO_MONITOR_CYCLE,
            payload={"trigger": "manual"},
            organization_id=org_id,
            dedupe_key=f"noexec-{uuid.uuid4()}",
        )
        await db.commit()
    async with AsyncSessionLocal() as db:
        job = await db.get(BackgroundJob, job.id)
        with patch.object(SeoMonitoringService, "_run_search_console_sync", new=AsyncMock(return_value={"completed": False})):
            with patch.object(
                SeoMonitoringService,
                "_schedule_crawl",
                new=AsyncMock(return_value={"enqueued": False, "reason": "NO_SITE_ROOT"}),
            ):
                result = await handle_seo_monitor_cycle(db, job)
                await db.commit()
    assert "run_id" in result
    import app.services.seo_monitoring_service as mod

    source = open(mod.__file__).read()
    assert "ActionService" not in source
    assert "ExecutionEngine" not in source
    assert "does not approve" in mod.DISCLAIMER.lower() or "does not execute" in mod.DISCLAIMER.lower()


def test_registered_monitor_job_types():
    assert SEO_MONITOR_SCHEDULER_TICK in registry.registered_job_types()
    assert SEO_MONITOR_CYCLE in registry.registered_job_types()
