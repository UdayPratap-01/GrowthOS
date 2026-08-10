"""P1-3 — background worker and asynchronous job dispatch."""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import delete, select

from app.core.config import Settings, get_settings
from app.db.session import AsyncSessionLocal
from app.jobs import registry
from app.jobs.queue import JobQueue
from app.models.automation import BackgroundJob
from app.models.enums import JobStatus
from app.worker import Worker


@pytest.fixture(autouse=True)
async def _clean_queue():
    async with AsyncSessionLocal() as db:
        await db.execute(delete(BackgroundJob))
        await db.commit()
    yield


async def _enqueue(job_type: str = "test_job", **kwargs) -> BackgroundJob:
    async with AsyncSessionLocal() as db:
        job = await JobQueue(db).enqueue(job_type=job_type, payload=kwargs.pop("payload", {}), **kwargs)
        await db.commit()
        return job


async def _reload(job_id) -> BackgroundJob:
    async with AsyncSessionLocal() as db:
        return await db.get(BackgroundJob, job_id)


def _aware(value: datetime) -> datetime:
    """SQLite drops tzinfo on round-trip; normalise before comparing."""
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


@pytest.fixture
def patched_build(monkeypatch):
    """Install extra handlers into the queue the worker builds."""

    def install(handlers: dict):
        real_build = registry.build_queue

        def build(db, **kwargs):
            queue = real_build(db, **kwargs)
            for job_type, handler in handlers.items():
                queue.register(job_type, handler)
            return queue

        monkeypatch.setattr("app.worker.build_queue", build)

    return install


# --------------------------------------------------------------------------
# Worker execution
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_worker_executes_a_queued_job(patched_build):
    ran: list[str] = []

    async def handler(_db, job):
        ran.append(str(job.id))
        return {"ok": True}

    patched_build({"wt_ok": handler})
    job = await _enqueue("wt_ok")

    executed = await Worker(poll_interval=0.01).run_once()

    assert executed == 1
    assert ran == [str(job.id)]
    reloaded = await _reload(job.id)
    assert reloaded.status == JobStatus.completed
    assert reloaded.result == {"ok": True}
    assert reloaded.finished_at is not None
    assert reloaded.locked_by is None, "the lease must be released on completion"


@pytest.mark.asyncio
async def test_failed_job_is_scheduled_for_retry_with_backoff(patched_build):
    async def handler(_db, _job):
        raise RuntimeError("provider exploded")

    patched_build({"wt_fail": handler})
    job = await _enqueue("wt_fail", max_attempts=3)

    await Worker(poll_interval=0.01).run_once()

    reloaded = await _reload(job.id)
    assert reloaded.status == JobStatus.retrying
    assert reloaded.attempts == 1
    assert "provider exploded" in reloaded.error
    assert _aware(reloaded.run_after) > datetime.now(timezone.utc), "retry must be delayed"


@pytest.mark.asyncio
async def test_backoff_grows_between_attempts(patched_build):
    async def handler(_db, _job):
        raise RuntimeError("still broken")

    patched_build({"wt_backoff": handler})
    job = await _enqueue("wt_backoff", max_attempts=5)
    worker = Worker(poll_interval=0.01)

    delays = []
    for _ in range(3):
        async with AsyncSessionLocal() as db:
            # Make the job due again so the next attempt can be claimed.
            row = await db.get(BackgroundJob, job.id)
            row.run_after = datetime.now(timezone.utc) - timedelta(seconds=1)
            await db.commit()
        before = datetime.now(timezone.utc)
        await worker.run_once()
        row = await _reload(job.id)
        delays.append((_aware(row.run_after) - before).total_seconds())

    assert delays[0] < delays[1] < delays[2], f"backoff did not grow: {delays}"


@pytest.mark.asyncio
async def test_job_fails_permanently_after_max_attempts(patched_build):
    async def handler(_db, _job):
        raise RuntimeError("always fails")

    patched_build({"wt_exhaust": handler})
    job = await _enqueue("wt_exhaust", max_attempts=2)
    worker = Worker(poll_interval=0.01)

    for _ in range(3):
        async with AsyncSessionLocal() as db:
            row = await db.get(BackgroundJob, job.id)
            if row.status in {JobStatus.failed, JobStatus.completed}:
                break
            row.run_after = datetime.now(timezone.utc) - timedelta(seconds=1)
            await db.commit()
        await worker.run_once()

    reloaded = await _reload(job.id)
    assert reloaded.status == JobStatus.failed
    assert reloaded.attempts == 2
    assert "always fails" in reloaded.error
    assert reloaded.finished_at is not None


@pytest.mark.asyncio
async def test_unknown_job_type_fails_with_a_reason_and_does_not_retry():
    job = await _enqueue("wt_no_such_handler")
    await Worker(poll_interval=0.01).run_once()

    reloaded = await _reload(job.id)
    assert reloaded.status == JobStatus.failed
    assert "No handler" in reloaded.error
    assert reloaded.attempts == 1, "an unrunnable job must not burn every retry"


@pytest.mark.asyncio
async def test_cancelled_job_is_never_executed(patched_build):
    ran: list[str] = []

    async def handler(_db, job):
        ran.append(str(job.id))
        return {}

    patched_build({"wt_cancel": handler})
    job = await _enqueue("wt_cancel")

    async with AsyncSessionLocal() as db:
        await JobQueue(db).cancel(job.id)
        await db.commit()

    await Worker(poll_interval=0.01).run_once()

    assert ran == []
    assert (await _reload(job.id)).status == JobStatus.cancelled


# --------------------------------------------------------------------------
# Crash recovery and concurrency
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_worker_crash_leaves_an_expired_lease_that_is_reclaimed(patched_build):
    """
    A killed worker must not strand its job in RUNNING forever.

    The crash is simulated by claiming a job and never finishing it, then letting
    the lease lapse.
    """
    ran: list[str] = []

    async def handler(_db, job):
        ran.append("second-worker")
        return {"recovered": True}

    patched_build({"wt_crash": handler})
    job = await _enqueue("wt_crash", max_attempts=3)

    async with AsyncSessionLocal() as db:
        claimed = await JobQueue(db, worker_id="doomed", lease_seconds=1).claim(job.id)
        assert claimed is not None
        await db.commit()

    # Expire the lease as if the worker died holding it.
    async with AsyncSessionLocal() as db:
        row = await db.get(BackgroundJob, job.id)
        row.lease_expires_at = datetime.now(timezone.utc) - timedelta(seconds=5)
        await db.commit()

    worker = Worker(poll_interval=0.01)
    await worker.run_once()

    assert worker.stats.reclaimed >= 1
    reloaded = await _reload(job.id)
    assert reloaded.status in {JobStatus.completed, JobStatus.retrying}
    if reloaded.status == JobStatus.completed:
        assert ran == ["second-worker"]


@pytest.mark.asyncio
async def test_concurrent_workers_run_a_job_exactly_once(patched_build):
    invocations: list[str] = []

    async def handler(_db, _job):
        invocations.append("run")
        await asyncio.sleep(0.01)
        return {}

    patched_build({"wt_race": handler})
    job = await _enqueue("wt_race")

    workers = [Worker(worker_id=f"w{i}", poll_interval=0.01) for i in range(6)]
    await asyncio.gather(*(w.run_once() for w in workers), return_exceptions=True)

    assert len(invocations) == 1, f"job executed {len(invocations)} times"
    assert (await _reload(job.id)).attempts == 1


@pytest.mark.asyncio
async def test_a_claimed_job_cannot_be_claimed_again():
    job = await _enqueue("wt_claimed")
    async with AsyncSessionLocal() as db:
        first = await JobQueue(db, worker_id="a").claim(job.id)
        await db.commit()
    async with AsyncSessionLocal() as db:
        second = await JobQueue(db, worker_id="b").claim(job.id)
        await db.commit()

    assert first is not None and second is None


@pytest.mark.asyncio
async def test_one_poisoned_job_does_not_stop_the_others(patched_build):
    order: list[str] = []

    async def good(_db, job):
        order.append("good")
        return {}

    async def bad(_db, _job):
        raise RuntimeError("boom")

    patched_build({"wt_good": good, "wt_bad": bad})
    await _enqueue("wt_bad")
    await _enqueue("wt_good")

    await Worker(poll_interval=0.01, batch_size=5).run_once()

    assert order == ["good"]


@pytest.mark.asyncio
async def test_worker_survives_a_handler_that_corrupts_the_session(patched_build):
    async def handler(db, _job):
        await db.execute(select(BackgroundJob).where(BackgroundJob.id == "not-a-uuid"))
        return {}

    patched_build({"wt_corrupt": handler})
    job = await _enqueue("wt_corrupt")

    worker = Worker(poll_interval=0.01)
    await worker.run_once()
    # The next cycle must still function on a fresh session.
    assert await worker.run_once() >= 0
    assert (await _reload(job.id)) is not None


# --------------------------------------------------------------------------
# Shutdown
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_forever_stops_when_asked():
    worker = Worker(poll_interval=0.01)
    task = asyncio.create_task(worker.run_forever())
    await asyncio.sleep(0.05)
    worker.request_stop()
    stats = await asyncio.wait_for(task, timeout=5)
    assert stats.cycles >= 1


@pytest.mark.asyncio
async def test_stop_does_not_abandon_an_in_flight_job(patched_build):
    started = asyncio.Event()

    async def slow(_db, _job):
        started.set()
        await asyncio.sleep(0.2)
        return {"finished": True}

    patched_build({"wt_slow": slow})
    job = await _enqueue("wt_slow")

    worker = Worker(poll_interval=0.01)
    task = asyncio.create_task(worker.run_forever())
    await asyncio.wait_for(started.wait(), timeout=5)
    worker.request_stop()
    await asyncio.wait_for(task, timeout=10)

    reloaded = await _reload(job.id)
    assert reloaded.status == JobStatus.completed, "shutdown must not drop a claimed job"


# --------------------------------------------------------------------------
# Idempotency
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dedupe_key_makes_enqueue_idempotent():
    async with AsyncSessionLocal() as db:
        queue = JobQueue(db)
        first = await queue.enqueue(job_type="wt_dedupe", payload={"n": 1}, dedupe_key="same")
        second = await queue.enqueue(job_type="wt_dedupe", payload={"n": 2}, dedupe_key="same")
        await db.commit()
        assert first.id == second.id

    async with AsyncSessionLocal() as db:
        count = len((await db.execute(select(BackgroundJob).where(BackgroundJob.dedupe_key == "same"))).scalars().all())
    assert count == 1


@pytest.mark.asyncio
async def test_jobs_without_a_dedupe_key_are_independent():
    async with AsyncSessionLocal() as db:
        queue = JobQueue(db)
        a = await queue.enqueue(job_type="wt_indep", payload={})
        b = await queue.enqueue(job_type="wt_indep", payload={})
        await db.commit()
    assert a.id != b.id


# --------------------------------------------------------------------------
# Configuration
# --------------------------------------------------------------------------


def test_production_never_runs_jobs_inline():
    assert Settings(environment="production", inline_job_execution=True).should_run_jobs_inline is False
    assert Settings(environment="development").should_run_jobs_inline is True
    assert Settings(environment="staging").should_run_jobs_inline is False


def test_production_startup_rejects_inline_execution():
    from app.core.startup_checks import ConfigurationError, validate_configuration

    settings = Settings(
        environment="production",
        secret_key="9f2c41b7ae0d63528c1fb47e0a95d3ce7b6108fa24d9e35c07bd18492af6c3e5",
        encryption_key="c07bd18492af6c3e59f2c41b7ae0d63528c1fb47e0a95d3ce7b6108fa24d9e35",
        ai_provider="openai",
        openai_api_key="sk-test",
        database_url="postgresql+asyncpg://u:p@db:5432/growthos",
        api_cors_origins="https://app.example.com",
        redis_url="redis://cache:6379/0",
        storage_backend="s3",
        s3_bucket="assets",
        inline_job_execution=True,
    )
    with pytest.raises(ConfigurationError) as exc:
        validate_configuration(settings)
    assert "INLINE_JOB_EXECUTION" in str(exc.value)


def test_every_registered_job_type_has_a_handler():
    """A job type that is enqueued but unregistered would fail at runtime."""

    class _StubSession:
        bind = None

    queue = registry.build_queue(_StubSession())
    missing = [t for t in registry.registered_job_types() if t not in queue._handlers]
    assert not missing, f"unregistered job types: {missing}"


# --------------------------------------------------------------------------
# HTTP contract: enqueue, do not block
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_video_generation_returns_immediately_with_a_job_to_poll(monkeypatch):
    """
    The audit finding: video generation blocked the request for up to ~62s.

    With worker execution the endpoint must return without invoking the provider.
    """
    from httpx import ASGITransport, AsyncClient

    from app.core.security import hash_password
    from app.main import app
    from app.models.client import Client
    from app.models.enums import MemberRole
    from app.models.organization import Organization, OrganizationMember
    from app.models.user import User

    monkeypatch.setattr(get_settings(), "inline_job_execution", False, raising=False)

    provider_called: list[str] = []

    class NeverCalledProvider:
        name = "replicate"

        def configured(self):
            return True

        async def generate_video(self, **_):
            provider_called.append("generate")
            raise AssertionError("the provider must not be called from the HTTP request")

    monkeypatch.setattr(
        "app.services.media_generation_service.get_video_provider", lambda: NeverCalledProvider()
    )

    password = "Str0ng-Test-Passw0rd!"
    suffix = uuid.uuid4().hex[:8]
    async with AsyncSessionLocal() as db:
        org = Organization(name=f"Async {suffix}", slug=f"async-{suffix}", demo_mode=False)
        db.add(org)
        await db.flush()
        email = f"async-{suffix}@jobs.test.com"
        user = User(email=email, hashed_password=hash_password(password), full_name="Async")
        db.add(user)
        await db.flush()
        db.add(OrganizationMember(organization_id=org.id, user_id=user.id, role=MemberRole.owner))
        client_row = Client(organization_id=org.id, business_name="Async Co", industry="saas")
        db.add(client_row)
        await db.commit()
        client_id = client_row.id
        org_id = org.id

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as http:
        token = (await http.post("/api/v1/auth/login", json={"email": email, "password": password})).json()
        headers = {"Authorization": f"Bearer {token['access_token']}"}
        response = await http.post(
            "/api/v1/creative/videos/generate",
            headers=headers,
            json={"client_id": str(client_id), "prompt": "a 10s product teaser", "duration_seconds": 10},
        )
        body = response.json()

        assert response.status_code == 200, response.text
        assert provider_called == [], "the request must not wait on the provider"
        assert body["status"] == "QUEUED"
        assert body["job_id"]

        # The job is pollable and reports a non-terminal state.
        poll = await http.get(f"/api/v1/creative/videos/jobs/{body['job_id']}", headers=headers)
        assert poll.status_code == 200
        assert poll.json()["status"] in {"QUEUED", "SUBMITTED", "PROCESSING"}

    async with AsyncSessionLocal() as db:
        queued = (
            await db.execute(
                select(BackgroundJob).where(
                    BackgroundJob.organization_id == org_id,
                    BackgroundJob.job_type == registry.VIDEO_GENERATE,
                )
            )
        ).scalars().all()
    assert len(queued) == 1, "exactly one worker job should have been enqueued"
    assert queued[0].payload["video_job_id"] == body["job_id"]


@pytest.mark.asyncio
async def test_job_status_endpoint_is_organization_scoped():
    from httpx import ASGITransport, AsyncClient

    from app.core.security import hash_password
    from app.main import app
    from app.models.enums import MemberRole
    from app.models.organization import Organization, OrganizationMember
    from app.models.user import User

    password = "Str0ng-Test-Passw0rd!"
    suffix = uuid.uuid4().hex[:8]
    async with AsyncSessionLocal() as db:
        victim = Organization(name=f"V {suffix}", slug=f"v-{suffix}", demo_mode=False)
        attacker = Organization(name=f"A {suffix}", slug=f"a-{suffix}", demo_mode=False)
        db.add_all([victim, attacker])
        await db.flush()
        email = f"nosy-{suffix}@evil.test.com"
        user = User(email=email, hashed_password=hash_password(password), full_name="Nosy")
        db.add(user)
        await db.flush()
        db.add(OrganizationMember(organization_id=attacker.id, user_id=user.id, role=MemberRole.owner))
        victim_job = await JobQueue(db).enqueue(
            job_type="wt_secret", payload={"secret": "x"}, organization_id=victim.id
        )
        await db.commit()
        victim_job_id = victim_job.id

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as http:
        token = (await http.post("/api/v1/auth/login", json={"email": email, "password": password})).json()
        headers = {"Authorization": f"Bearer {token['access_token']}"}
        assert (await http.get(f"/api/v1/jobs/{victim_job_id}", headers=headers)).status_code == 404
        assert (await http.post(f"/api/v1/jobs/{victim_job_id}/cancel", headers=headers)).status_code == 404
        assert (await http.get("/api/v1/jobs", headers=headers)).json() == []
