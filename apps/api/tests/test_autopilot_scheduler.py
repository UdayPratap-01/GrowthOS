"""Scheduled autopilot execution — scheduler tick, cycle jobs, idempotency, tenant safety."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, select, update

from app.core.config import Settings, get_settings
from app.core.security import hash_password
from app.core.startup_checks import ConfigurationError, validate_configuration
from app.db.session import AsyncSessionLocal
from app.jobs import handlers, registry
from app.jobs.autopilot_scheduler import (
    AUTOPILOT_CYCLE,
    AUTOPILOT_SCHEDULER_TICK,
    autopilot_cycle_dedupe_key,
    discover_autopilot_targets,
    enqueue_autopilot_cycle,
    ensure_scheduler_tick,
    organization_has_inflight_autopilot_cycle,
    scheduled_window_start,
    scheduler_tick_dedupe_key,
    validate_autopilot_scheduler_settings,
)
from app.jobs.queue import JobQueue
from app.main import app
from app.models.automation import AutonomySettings, BackgroundJob
from app.models.client import Client
from app.models.enums import ClientStatus, JobStatus, MemberRole
from app.models.organization import Organization, OrganizationMember
from app.models.user import User
from app.worker import Worker


@pytest.fixture
def patched_build(monkeypatch):
    """Install extra handlers into the queue the worker builds."""

    def install(handlers_map: dict):
        real_build = registry.build_queue

        def build(db, **kwargs):
            queue = real_build(db, **kwargs)
            for job_type, handler in handlers_map.items():
                queue.register(job_type, handler)
            return queue

        monkeypatch.setattr("app.worker.build_queue", build)

    return install


@pytest.fixture(autouse=True)
async def _clean_jobs():
    async with AsyncSessionLocal() as db:
        await db.execute(delete(BackgroundJob))
        await db.execute(update(AutonomySettings).values(automation_enabled=False))
        await db.commit()
    yield


def _enable_scheduler(monkeypatch, **overrides):
    settings = get_settings()
    monkeypatch.setattr(settings, "autopilot_scheduler_enabled", overrides.pop("autopilot_scheduler_enabled", True), raising=False)
    for key, value in {
        "autopilot_interval_minutes": 60,
        "autopilot_max_orgs_per_cycle": 10,
        **overrides,
    }.items():
        monkeypatch.setattr(settings, key, value, raising=False)
    return settings


def _disable_scheduler(monkeypatch):
    monkeypatch.setattr(get_settings(), "autopilot_scheduler_enabled", False, raising=False)


async def _seed_org_client(*, demo_mode: bool = True, automation_enabled: bool = False):
    async with AsyncSessionLocal() as db:
        org = Organization(
            name=f"Sched Org {uuid.uuid4().hex[:6]}",
            slug=f"sched-{uuid.uuid4().hex[:8]}",
            demo_mode=demo_mode,
        )
        user = User(
            email=f"sched-{uuid.uuid4().hex[:8]}@test.com",
            hashed_password=hash_password("pass"),
            full_name="Scheduler Tester",
        )
        db.add_all([org, user])
        await db.flush()
        db.add(OrganizationMember(organization_id=org.id, user_id=user.id, role=MemberRole.owner))
        client = Client(organization_id=org.id, business_name="Sched Client", industry="saas")
        db.add(client)
        await db.flush()
        settings = AutonomySettings(
            organization_id=org.id,
            client_id=None,
            automation_enabled=automation_enabled,
        )
        db.add(settings)
        await db.commit()
        return org.id, client.id


# --------------------------------------------------------------------------
# Configuration
# --------------------------------------------------------------------------


def test_scheduler_disabled_by_default():
    settings = get_settings()
    assert settings.autopilot_scheduler_enabled is False


def test_validate_scheduler_interval_must_be_positive():
    errors = validate_autopilot_scheduler_settings(
        Settings(autopilot_interval_minutes=0, autopilot_max_orgs_per_cycle=10)
    )
    assert any("positive" in e.lower() for e in errors)


def test_validate_scheduler_interval_rejects_dangerously_short():
    errors = validate_autopilot_scheduler_settings(
        Settings(autopilot_interval_minutes=1, autopilot_max_orgs_per_cycle=10)
    )
    assert any("at least" in e.lower() for e in errors)


def test_validate_scheduler_max_orgs_must_be_positive():
    errors = validate_autopilot_scheduler_settings(
        Settings(autopilot_interval_minutes=60, autopilot_max_orgs_per_cycle=0)
    )
    assert any("positive" in e.lower() for e in errors)


def test_validate_scheduler_config_ok_when_enabled():
    errors = validate_autopilot_scheduler_settings(
        Settings(
            autopilot_scheduler_enabled=True,
            autopilot_interval_minutes=60,
            autopilot_max_orgs_per_cycle=10,
        )
    )
    assert errors == []


def test_startup_rejects_invalid_scheduler_interval(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "autopilot_scheduler_enabled", True, raising=False)
    monkeypatch.setattr(settings, "autopilot_interval_minutes", 1, raising=False)
    with pytest.raises(ConfigurationError) as exc:
        validate_configuration(settings)
    assert "AUTOPILOT_INTERVAL_MINUTES" in str(exc.value)


# --------------------------------------------------------------------------
# Window / dedupe keys
# --------------------------------------------------------------------------


def test_scheduled_window_start_floors_to_interval():
    now = datetime(2026, 8, 27, 10, 37, tzinfo=timezone.utc)
    window = scheduled_window_start(now, interval_minutes=60)
    assert window.minute == 0
    assert window.hour == 10


def test_scheduler_tick_dedupe_key_is_deterministic():
    window = datetime(2026, 8, 27, 10, 0, tzinfo=timezone.utc)
    assert scheduler_tick_dedupe_key(window) == scheduler_tick_dedupe_key(window)


def test_autopilot_cycle_dedupe_key_scopes_org_client_window():
    org_id = uuid.uuid4()
    client_id = uuid.uuid4()
    window = datetime(2026, 8, 27, 10, 0, tzinfo=timezone.utc)
    key = autopilot_cycle_dedupe_key(org_id, client_id, window)
    assert str(org_id) in key
    assert str(client_id) in key


# --------------------------------------------------------------------------
# Discovery and enqueue
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_discover_skips_automation_disabled_org():
    org_id, client_id = await _seed_org_client(automation_enabled=False)
    async with AsyncSessionLocal() as db:
        org = await db.get(Organization, org_id)
        targets = await discover_autopilot_targets(db, max_orgs=10)
    assert all(t[0].id != org.id for t in targets)


@pytest.mark.asyncio
async def test_discover_includes_automation_enabled_org():
    org_id, client_id = await _seed_org_client(automation_enabled=True)
    async with AsyncSessionLocal() as db:
        targets = await discover_autopilot_targets(db, max_orgs=10)
    assert any(t[0].id == org_id and t[1].id == client_id for t in targets)


@pytest.mark.asyncio
async def test_max_orgs_per_cycle_limits_discovery():
    org_ids = []
    for _ in range(3):
        org_id, _ = await _seed_org_client(automation_enabled=True)
        org_ids.append(org_id)
    async with AsyncSessionLocal() as db:
        targets = await discover_autopilot_targets(db, max_orgs=2)
    discovered_orgs = {t[0].id for t in targets}
    assert len(discovered_orgs) <= 2


@pytest.mark.asyncio
async def test_enqueue_cycle_is_idempotent_per_window(monkeypatch):
    org_id, client_id = await _seed_org_client(automation_enabled=True)
    window = datetime(2026, 8, 27, 12, 0, tzinfo=timezone.utc)
    async with AsyncSessionLocal() as db:
        org = await db.get(Organization, org_id)
        client = await db.get(Client, client_id)
        first = await enqueue_autopilot_cycle(db, organization=org, client=client, window_start=window)
        second = await enqueue_autopilot_cycle(db, organization=org, client=client, window_start=window)
        await db.commit()
    assert first.job is not None
    assert second.job is not None
    assert first.job.id == second.job.id


@pytest.mark.asyncio
async def test_overlapping_cycle_prevented_while_inflight():
    org_id, client_id = await _seed_org_client(automation_enabled=True)
    window = datetime(2026, 8, 27, 12, 0, tzinfo=timezone.utc)
    async with AsyncSessionLocal() as db:
        org = await db.get(Organization, org_id)
        client = await db.get(Client, client_id)
        inflight = BackgroundJob(
            organization_id=org_id,
            job_type=AUTOPILOT_CYCLE,
            status=JobStatus.running,
            payload={"client_id": str(client_id)},
        )
        db.add(inflight)
        await db.flush()
        assert await organization_has_inflight_autopilot_cycle(db, org_id)

        other_window = datetime(2026, 8, 27, 13, 0, tzinfo=timezone.utc)
        result = await enqueue_autopilot_cycle(
            db, organization=org, client=client, window_start=other_window
        )
        assert result.skipped is True
        assert result.reason == "OVERLAPPING_CYCLE"


# --------------------------------------------------------------------------
# Scheduler tick handler
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_scheduler_tick_skipped_when_disabled(monkeypatch):
    _disable_scheduler(monkeypatch)
    async with AsyncSessionLocal() as db:
        job = await JobQueue(db).enqueue(
            job_type=AUTOPILOT_SCHEDULER_TICK,
            payload={},
            organization_id=None,
        )
        result = await handlers.handle_autopilot_scheduler_tick(db, job)
        await db.commit()
    assert result["skipped"] is True


@pytest.mark.asyncio
async def test_scheduler_tick_enqueues_enabled_orgs(monkeypatch):
    _enable_scheduler(monkeypatch)
    org_id, client_id = await _seed_org_client(automation_enabled=True)
    async with AsyncSessionLocal() as db:
        tick = await JobQueue(db).enqueue(
            job_type=AUTOPILOT_SCHEDULER_TICK,
            payload={"window": datetime(2026, 8, 27, 12, 0, tzinfo=timezone.utc).isoformat()},
            organization_id=None,
        )
        result = await handlers.handle_autopilot_scheduler_tick(db, tick)
        await db.commit()
        cycle_jobs = (
            await db.execute(
                select(BackgroundJob).where(
                    BackgroundJob.job_type == AUTOPILOT_CYCLE,
                    BackgroundJob.organization_id == org_id,
                )
            )
        ).scalars().all()
    assert result["enqueued"] >= 1
    assert len(cycle_jobs) >= 1
    assert cycle_jobs[0].payload["client_id"] == str(client_id)


@pytest.mark.asyncio
async def test_scheduler_tick_failure_isolation(monkeypatch):
    _enable_scheduler(monkeypatch)
    org_a, _ = await _seed_org_client(automation_enabled=True)
    org_b, _ = await _seed_org_client(automation_enabled=True)

    from app.jobs import autopilot_scheduler as scheduler_module

    original = scheduler_module.enqueue_autopilot_cycle
    call_count = {"n": 0}

    async def flaky_enqueue(db, *, organization, client, window_start):
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise RuntimeError("simulated enqueue failure")
        return await original(db, organization=organization, client=client, window_start=window_start)

    monkeypatch.setattr(scheduler_module, "enqueue_autopilot_cycle", flaky_enqueue)

    async with AsyncSessionLocal() as db:
        tick = await JobQueue(db).enqueue(
            job_type=AUTOPILOT_SCHEDULER_TICK,
            payload={"window": datetime(2026, 8, 27, 12, 0, tzinfo=timezone.utc).isoformat()},
            organization_id=None,
        )
        result = await handlers.handle_autopilot_scheduler_tick(db, tick)
        await db.commit()
    assert result["failures"] >= 1
    assert result["enqueued"] >= 1


@pytest.mark.asyncio
async def test_duplicate_scheduler_tick_dedupe():
    window = datetime(2026, 8, 27, 12, 0, tzinfo=timezone.utc)
    dedupe = scheduler_tick_dedupe_key(window)
    async with AsyncSessionLocal() as db:
        first = await JobQueue(db).enqueue(
            job_type=AUTOPILOT_SCHEDULER_TICK,
            payload={"window": window.isoformat()},
            organization_id=None,
            dedupe_key=dedupe,
        )
        second = await JobQueue(db).enqueue(
            job_type=AUTOPILOT_SCHEDULER_TICK,
            payload={"window": window.isoformat()},
            organization_id=None,
            dedupe_key=dedupe,
        )
        await db.commit()
    assert first.id == second.id


@pytest.mark.asyncio
async def test_ensure_scheduler_tick_idempotent(monkeypatch):
    _enable_scheduler(monkeypatch)
    async with AsyncSessionLocal() as db:
        first = await ensure_scheduler_tick(db)
        second = await ensure_scheduler_tick(db)
        await db.commit()
    assert first is not None
    assert second is not None
    assert first.id == second.id


# --------------------------------------------------------------------------
# Cycle handler invokes orchestrator
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cycle_handler_invokes_orchestrator(monkeypatch):
    org_id, client_id = await _seed_org_client(automation_enabled=True, demo_mode=True)
    mock_run = AsyncMock(
        return_value=type(
            "R",
            (),
            {
                "cycle_id": "cycle-1",
                "actions_created": 2,
                "actions_blocked": 0,
                "errors": [],
                "model_dump": lambda self, mode="json": {
                    "cycle_id": "cycle-1",
                    "actions_created": 2,
                    "actions_blocked": 0,
                    "errors": [],
                },
            },
        )()
    )
    monkeypatch.setattr(
        "app.services.autopilot_orchestrator_service.AutopilotOrchestratorService",
        lambda db: type("S", (), {"run_cycle": mock_run})(),
    )

    async with AsyncSessionLocal() as db:
        org = await db.get(Organization, org_id)
        job = await JobQueue(db).enqueue(
            job_type=AUTOPILOT_CYCLE,
            payload={"client_id": str(client_id), "trigger": "scheduler"},
            organization_id=org_id,
        )
        result = await handlers.handle_autopilot_cycle(db, job)
        await db.commit()

    mock_run.assert_awaited_once()
    call_kwargs = mock_run.await_args.kwargs
    assert call_kwargs["client_id"] == client_id
    assert result["actions_created"] == 2


@pytest.mark.asyncio
async def test_cycle_handler_rejects_cross_tenant_client():
    org_a, client_a = await _seed_org_client(automation_enabled=True)
    org_b, _ = await _seed_org_client(automation_enabled=True)
    async with AsyncSessionLocal() as db:
        job = await JobQueue(db).enqueue(
            job_type=AUTOPILOT_CYCLE,
            payload={"client_id": str(client_a)},
            organization_id=org_b,
        )
        with pytest.raises(handlers.UnrecoverableJobError):
            await handlers.handle_autopilot_cycle(db, job)


@pytest.mark.asyncio
async def test_cycle_handler_skips_automation_disabled():
    org_id, client_id = await _seed_org_client(automation_enabled=False)
    async with AsyncSessionLocal() as db:
        job = await JobQueue(db).enqueue(
            job_type=AUTOPILOT_CYCLE,
            payload={"client_id": str(client_id)},
            organization_id=org_id,
        )
        result = await handlers.handle_autopilot_cycle(db, job)
    assert result["skipped"] is True


@pytest.mark.asyncio
async def test_demo_org_cycle_preserves_demo_context(monkeypatch):
    org_id, client_id = await _seed_org_client(automation_enabled=True, demo_mode=True)
    captured = {}

    class _StubOrchestrator:
        async def run_cycle(self, org, **kwargs):
            captured["demo_mode"] = org.demo_mode
            from app.schemas.autopilot import AutopilotCycleResult

            return AutopilotCycleResult(
                cycle_id="x",
                organization_id=org.id,
                client_id=kwargs["client_id"],
                run_id=None,
                started_at=datetime.now(timezone.utc),
                completed_at=datetime.now(timezone.utc),
                iterations=1,
                actions_created=0,
                actions_executed=0,
                actions_blocked=0,
                errors=[],
                analytics_data_source="demo",
                message="ok",
            )

    monkeypatch.setattr(
        "app.services.autopilot_orchestrator_service.AutopilotOrchestratorService",
        lambda db: _StubOrchestrator(),
    )

    async with AsyncSessionLocal() as db:
        job = await JobQueue(db).enqueue(
            job_type=AUTOPILOT_CYCLE,
            payload={"client_id": str(client_id)},
            organization_id=org_id,
        )
        await handlers.handle_autopilot_cycle(db, job)
    assert captured["demo_mode"] is True


# --------------------------------------------------------------------------
# Worker integration
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_worker_does_not_schedule_when_disabled(monkeypatch):
    _disable_scheduler(monkeypatch)
    async with AsyncSessionLocal() as db:
        before = await db.scalar(select(BackgroundJob.id))
    await Worker(poll_interval=0.01).run_once()
    async with AsyncSessionLocal() as db:
        tick = await db.scalar(
            select(BackgroundJob).where(BackgroundJob.job_type == AUTOPILOT_SCHEDULER_TICK)
        )
    assert tick is None


@pytest.mark.asyncio
async def test_worker_ensures_scheduler_tick_when_enabled(monkeypatch, patched_build):
    _enable_scheduler(monkeypatch)

    async def noop_handler(_db, _job):
        return {"ok": True}

    patched_build({AUTOPILOT_SCHEDULER_TICK: noop_handler})
    await Worker(poll_interval=0.01).run_once()
    async with AsyncSessionLocal() as db:
        tick = await db.scalar(
            select(BackgroundJob).where(BackgroundJob.job_type == AUTOPILOT_SCHEDULER_TICK)
        )
    assert tick is not None
    assert tick.status in {JobStatus.queued, JobStatus.completed, JobStatus.running}


# --------------------------------------------------------------------------
# Manual POST /autopilot/cycle unchanged
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_manual_autopilot_cycle_endpoint_still_works():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        login = await client.post(
            "/api/v1/auth/login",
            json={"email": "demo@growthos.ai", "password": "demo1234"},
        )
        assert login.status_code == 200
        headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
        clients = await client.get("/api/v1/clients", headers=headers)
        client_id = clients.json()[0]["id"]

        with patch(
            "app.api.v1.autopilot.AutopilotOrchestratorService.run_cycle",
            new_callable=AsyncMock,
        ) as mock_cycle:
            from app.schemas.autopilot import AutopilotCycleResult

            mock_cycle.return_value = AutopilotCycleResult(
                cycle_id="manual",
                organization_id=uuid.uuid4(),
                client_id=uuid.UUID(client_id),
                run_id=None,
                started_at=datetime.now(timezone.utc),
                completed_at=datetime.now(timezone.utc),
                iterations=1,
                actions_created=1,
                actions_executed=0,
                actions_blocked=0,
                errors=[],
                analytics_data_source="demo",
                message="manual ok",
            )
            resp = await client.post(
                "/api/v1/autopilot/cycle",
                headers=headers,
                json={"client_id": client_id, "max_iterations": 1},
            )
        assert resp.status_code == 200
        assert resp.json()["cycle_id"] == "manual"
        mock_cycle.assert_awaited_once()
