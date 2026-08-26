"""P0-9 — job claiming, leases, retries and concurrency safety."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.db.session import AsyncSessionLocal
from app.jobs.queue import JobQueue
from app.models.automation import BackgroundJob
from app.models.enums import JobStatus


@pytest.fixture(autouse=True)
async def _isolate_queue():
    """
    `process_due` claims a global batch, so jobs left behind by other test
    modules can crowd out this module's job and make assertions order-dependent.
    """
    from sqlalchemy import delete

    async with AsyncSessionLocal() as db:
        await db.execute(delete(BackgroundJob))
        await db.commit()
    yield


async def _enqueue(job_type: str = "test_job", **kwargs) -> BackgroundJob:
    async with AsyncSessionLocal() as db:
        job = await JobQueue(db).enqueue(job_type=job_type, payload={}, **kwargs)
        await db.commit()
        return job


async def _reload(job_id) -> BackgroundJob:
    async with AsyncSessionLocal() as db:
        return await db.get(BackgroundJob, job_id)


# --------------------------------------------------------------------------
# Claiming
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_claim_marks_job_running_with_a_lease():
    job = await _enqueue()
    async with AsyncSessionLocal() as db:
        claimed = await JobQueue(db, worker_id="worker-a").claim(job.id)
        await db.commit()

    assert claimed is not None
    assert claimed.status == JobStatus.running
    assert claimed.locked_by == "worker-a"
    assert claimed.lease_expires_at is not None
    assert claimed.attempts == 1


@pytest.mark.asyncio
async def test_second_worker_cannot_claim_the_same_job():
    """The core duplicate-processing guard."""
    job = await _enqueue()

    async with AsyncSessionLocal() as db_a:
        first = await JobQueue(db_a, worker_id="worker-a").claim(job.id)
        await db_a.commit()

    async with AsyncSessionLocal() as db_b:
        second = await JobQueue(db_b, worker_id="worker-b").claim(job.id)
        await db_b.commit()

    assert first is not None, "first worker must win"
    assert second is None, "a running job must not be claimable by a second worker"

    reloaded = await _reload(job.id)
    assert reloaded.locked_by == "worker-a"
    assert reloaded.attempts == 1, "a lost race must not inflate the attempt count"


@pytest.mark.asyncio
async def test_concurrent_workers_execute_a_job_exactly_once():
    """Race eight workers at one job; exactly one handler invocation may occur."""
    job = await _enqueue(job_type="concurrent_job")
    invocations: list[str] = []

    async def worker(name: str) -> None:
        async def handler(_db, _job) -> dict:
            invocations.append(name)
            await asyncio.sleep(0.01)
            return {"by": name}

        async with AsyncSessionLocal() as db:
            queue = JobQueue(db, worker_id=name)
            queue.register("concurrent_job", handler)
            await queue.process_due(limit=5)
            await db.commit()

    await asyncio.gather(*(worker(f"worker-{i}") for i in range(8)), return_exceptions=True)

    assert len(invocations) == 1, f"job ran {len(invocations)} times: {invocations}"
    reloaded = await _reload(job.id)
    assert reloaded.status == JobStatus.completed
    assert reloaded.attempts == 1


@pytest.mark.asyncio
async def test_concurrent_workers_split_a_batch_without_overlap():
    jobs = [await _enqueue(job_type="batch_job") for _ in range(6)]
    ran: list[str] = []

    async def worker(name: str) -> None:
        async def handler(_db, job) -> dict:
            ran.append(str(job.id))
            return {}

        async with AsyncSessionLocal() as db:
            queue = JobQueue(db, worker_id=name)
            queue.register("batch_job", handler)
            await queue.process_due(limit=6)
            await db.commit()

    await asyncio.gather(*(worker(f"w{i}") for i in range(4)), return_exceptions=True)

    ours = [j for j in ran if j in {str(job.id) for job in jobs}]
    assert len(ours) == len(set(ours)), "no job may be processed twice"


# --------------------------------------------------------------------------
# Leases and crash recovery
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_expired_lease_makes_a_job_reclaimable():
    """A crashed worker must not orphan a job permanently."""
    job = await _enqueue()

    async with AsyncSessionLocal() as db:
        await JobQueue(db, worker_id="crashed", lease_seconds=1).claim(job.id)
        await db.commit()

    # Simulate the lease elapsing without the worker ever finishing.
    async with AsyncSessionLocal() as db:
        row = await db.get(BackgroundJob, job.id)
        row.lease_expires_at = datetime.now(timezone.utc) - timedelta(seconds=5)
        await db.commit()

    async with AsyncSessionLocal() as db:
        reclaimed = await JobQueue(db, worker_id="rescuer").claim(job.id)
        await db.commit()

    assert reclaimed is not None, "an expired lease must be reclaimable"
    assert reclaimed.locked_by == "rescuer"
    assert reclaimed.attempts == 2


@pytest.mark.asyncio
async def test_live_lease_blocks_reclaim():
    job = await _enqueue()
    async with AsyncSessionLocal() as db:
        await JobQueue(db, worker_id="owner", lease_seconds=600).claim(job.id)
        await db.commit()

    async with AsyncSessionLocal() as db:
        assert await JobQueue(db, worker_id="thief").claim(job.id) is None


@pytest.mark.asyncio
async def test_heartbeat_extends_lease_only_for_the_owner():
    job = await _enqueue()
    async with AsyncSessionLocal() as db:
        owner = JobQueue(db, worker_id="owner", lease_seconds=60)
        claimed = await owner.claim(job.id)
        original = claimed.lease_expires_at

        assert await owner.heartbeat(claimed) is True
        await db.commit()

    async with AsyncSessionLocal() as db:
        row = await db.get(BackgroundJob, job.id)
        assert row.lease_expires_at >= original
        assert row.heartbeat_at is not None

        stranger = JobQueue(db, worker_id="stranger", lease_seconds=60)
        assert await stranger.heartbeat(row) is False, "only the lease owner may renew"


@pytest.mark.asyncio
async def test_reap_expired_leases_requeues_recoverable_jobs():
    job = await _enqueue(max_attempts=3)
    async with AsyncSessionLocal() as db:
        await JobQueue(db, worker_id="dead").claim(job.id)
        row = await db.get(BackgroundJob, job.id)
        row.lease_expires_at = datetime.now(timezone.utc) - timedelta(minutes=10)
        await db.commit()

    async with AsyncSessionLocal() as db:
        reaped = await JobQueue(db).reap_expired_leases()
        await db.commit()

    assert reaped >= 1
    reloaded = await _reload(job.id)
    assert reloaded.status == JobStatus.retrying
    assert reloaded.locked_by is None


@pytest.mark.asyncio
async def test_reap_fails_jobs_that_exhausted_attempts():
    job = await _enqueue(max_attempts=1)
    async with AsyncSessionLocal() as db:
        await JobQueue(db, worker_id="dead").claim(job.id)
        row = await db.get(BackgroundJob, job.id)
        row.lease_expires_at = datetime.now(timezone.utc) - timedelta(minutes=10)
        await db.commit()

    async with AsyncSessionLocal() as db:
        await JobQueue(db).reap_expired_leases()
        await db.commit()

    reloaded = await _reload(job.id)
    assert reloaded.status == JobStatus.failed
    assert "lease expired" in (reloaded.error or "").lower()


# --------------------------------------------------------------------------
# Retry / failure / cancellation
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_handler_error_schedules_a_retry_then_fails_at_max_attempts():
    job = await _enqueue(job_type="flaky_job", max_attempts=2)

    async def boom(_db, _job) -> dict:
        raise RuntimeError("provider exploded")

    async with AsyncSessionLocal() as db:
        queue = JobQueue(db, worker_id="w1")
        queue.register("flaky_job", boom)
        claimed = await queue.claim(job.id)
        await queue._run_claimed(claimed)
        await db.commit()

    first = await _reload(job.id)
    assert first.status == JobStatus.retrying
    assert first.run_after is not None, "a retry must be scheduled, not run immediately"
    assert first.locked_by is None, "a failed attempt must release its lease"

    # Second (final) attempt.
    async with AsyncSessionLocal() as db:
        row = await db.get(BackgroundJob, job.id)
        row.run_after = datetime.now(timezone.utc) - timedelta(seconds=1)
        await db.commit()

    async with AsyncSessionLocal() as db:
        queue = JobQueue(db, worker_id="w2")
        queue.register("flaky_job", boom)
        claimed = await queue.claim(job.id)
        await queue._run_claimed(claimed)
        await db.commit()

    final = await _reload(job.id)
    assert final.status == JobStatus.failed
    assert final.attempts == 2
    assert "provider exploded" in final.error


@pytest.mark.asyncio
async def test_missing_handler_fails_without_retrying():
    job = await _enqueue(job_type="no_such_handler")
    async with AsyncSessionLocal() as db:
        queue = JobQueue(db)
        await queue.process_due(limit=5)
        await db.commit()

    reloaded = await _reload(job.id)
    assert reloaded.status == JobStatus.failed
    assert "No handler" in reloaded.error


@pytest.mark.asyncio
async def test_failed_job_can_be_recovered():
    job = await _enqueue(job_type="no_such_handler")
    async with AsyncSessionLocal() as db:
        await JobQueue(db).process_due(limit=5)
        await db.commit()
    assert (await _reload(job.id)).status == JobStatus.failed

    async with AsyncSessionLocal() as db:
        recovered = await JobQueue(db).retry(job.id, reset_attempts=True)
        await db.commit()

    assert recovered is not None
    assert recovered.status == JobStatus.queued
    assert recovered.attempts == 0
    assert recovered.error is None


@pytest.mark.asyncio
async def test_cancelled_job_is_never_executed():
    job = await _enqueue(job_type="cancel_me")
    ran: list[str] = []

    async def handler(_db, _job) -> dict:
        ran.append("x")
        return {}

    async with AsyncSessionLocal() as db:
        cancelled = await JobQueue(db).cancel(job.id)
        await db.commit()
    assert cancelled.status == JobStatus.cancelled

    async with AsyncSessionLocal() as db:
        queue = JobQueue(db)
        queue.register("cancel_me", handler)
        await queue.process_due(limit=10)
        await db.commit()

    assert ran == [], "a cancelled job must not run"
    assert (await _reload(job.id)).status == JobStatus.cancelled


@pytest.mark.asyncio
async def test_running_job_cannot_be_cancelled():
    job = await _enqueue()
    async with AsyncSessionLocal() as db:
        await JobQueue(db, worker_id="owner", lease_seconds=600).claim(job.id)
        await db.commit()

    async with AsyncSessionLocal() as db:
        assert await JobQueue(db).cancel(job.id) is None


@pytest.mark.asyncio
async def test_future_scheduled_job_is_not_picked_up():
    job = await _enqueue(job_type="later_job", run_after=datetime.now(timezone.utc) + timedelta(hours=1))
    ran: list[str] = []

    async def handler(_db, _job) -> dict:
        ran.append("x")
        return {}

    async with AsyncSessionLocal() as db:
        queue = JobQueue(db)
        queue.register("later_job", handler)
        await queue.process_due(limit=10)
        await db.commit()

    assert ran == []
    assert (await _reload(job.id)).status == JobStatus.queued


@pytest.mark.asyncio
async def test_completed_job_clears_its_lease():
    job = await _enqueue(job_type="ok_job")

    async def handler(_db, _job) -> dict:
        return {"ok": True}

    async with AsyncSessionLocal() as db:
        queue = JobQueue(db, worker_id="w")
        queue.register("ok_job", handler)
        await queue.process_due(limit=10)
        await db.commit()

    reloaded = await _reload(job.id)
    assert reloaded.status == JobStatus.completed
    assert reloaded.locked_by is None
    assert reloaded.lease_expires_at is None
    assert reloaded.finished_at is not None


@pytest.mark.asyncio
async def test_all_required_states_exist():
    for name in ("queued", "running", "completed", "failed", "retrying", "cancelled"):
        assert hasattr(JobStatus, name), f"JobStatus.{name} is required"


@pytest.mark.asyncio
async def test_no_job_is_left_running_after_processing():
    """Regression guard: the old queue set `running` before executing and could strand rows."""
    await _enqueue(job_type="sweep_job")

    async def handler(_db, _job) -> dict:
        return {}

    async with AsyncSessionLocal() as db:
        queue = JobQueue(db, worker_id="w")
        queue.register("sweep_job", handler)
        await queue.process_due(limit=50)
        await db.commit()

    async with AsyncSessionLocal() as db:
        stuck = (
            (
                await db.execute(
                    select(BackgroundJob).where(
                        BackgroundJob.job_type == "sweep_job",
                        BackgroundJob.status == JobStatus.running,
                    )
                )
            )
            .scalars()
            .all()
        )
    assert stuck == []
