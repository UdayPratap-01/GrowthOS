"""
Background job abstraction — DB-backed queue.

Safety model
------------
A job is only executed by a worker that has *claimed* it. Claiming is a
compare-and-swap UPDATE guarded by the row's current status, so two workers
racing for the same job produce exactly one winner on both PostgreSQL and
SQLite. On PostgreSQL the candidate scan additionally uses
`FOR UPDATE SKIP LOCKED` so concurrent workers pick disjoint batches instead
of contending for the same rows.

Claiming installs a lease (`locked_by` + `lease_expires_at`). If a worker dies
mid-job, the lease expires and the job becomes claimable again rather than
being orphaned in `running` forever.

Tenancy
-------
The worker process runs the queue unscoped: it is trusted infrastructure and
must drain every tenant's work. Anything reachable from an HTTP request must
pass `organization_id`, which is applied inside the claim predicate itself —
not merely in a pre-check — so a caller cannot claim, cancel or retry a job
belonging to another tenant even under a race.

State machine
-------------
    QUEUED ──claim──> RUNNING ──ok───> COMPLETED
      ^                  │
      │                  ├──error, attempts < max──> RETRYING ──due──> (claimable)
      │                  ├──error, attempts >= max─> FAILED
      │                  └──lease expired─────────> (reclaimable)
    FAILED ──retry()──> QUEUED
    QUEUED/RETRYING ──cancel()──> CANCELLED
"""

from __future__ import annotations

import os
import socket
import uuid
from datetime import datetime, timedelta, timezone
from typing import Awaitable, Callable
from uuid import UUID

from sqlalchemy import or_, select, true as True_, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.automation import BackgroundJob
from app.models.enums import JobStatus

JobHandler = Callable[[AsyncSession, BackgroundJob], Awaitable[dict]]

DEFAULT_LEASE_SECONDS = 300
MAX_BACKOFF_MINUTES = 60

# Statuses a claim may transition from. `running` is included so an expired
# lease can be reclaimed; the lease predicate is what makes that safe.
CLAIMABLE_STATUSES = (JobStatus.queued, JobStatus.retrying, JobStatus.running)
TERMINAL_STATUSES = (JobStatus.completed, JobStatus.failed, JobStatus.cancelled)


def default_worker_id() -> str:
    """Stable-per-process identifier so leases are attributable to a worker."""
    return f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:8]}"


class JobQueue:
    def __init__(
        self,
        db: AsyncSession,
        *,
        worker_id: str | None = None,
        lease_seconds: int = DEFAULT_LEASE_SECONDS,
    ) -> None:
        self.db = db
        self.worker_id = worker_id or default_worker_id()
        self.lease_seconds = lease_seconds
        self._handlers: dict[str, JobHandler] = {}

    def register(self, job_type: str, handler: JobHandler) -> None:
        self._handlers[job_type] = handler

    # ------------------------------------------------------------------
    # Enqueue
    # ------------------------------------------------------------------

    async def enqueue(
        self,
        *,
        job_type: str,
        payload: dict,
        organization_id: UUID | None = None,
        run_after: datetime | None = None,
        max_attempts: int = 3,
        dedupe_key: str | None = None,
    ) -> BackgroundJob:
        """
        Add a job to the queue.

        With `dedupe_key`, enqueuing is idempotent: a caller that retries after a
        timeout gets the original job back rather than a second copy of the work.
        The unique constraint is the real guard — two concurrent callers race at
        the database, and the loser reads the winner's row.
        """
        if dedupe_key:
            existing = await self.db.scalar(
                select(BackgroundJob).where(BackgroundJob.dedupe_key == dedupe_key)
            )
            if existing is not None:
                return existing

        job = BackgroundJob(
            organization_id=organization_id,
            job_type=job_type,
            status=JobStatus.queued,
            payload=payload,
            run_after=run_after,
            max_attempts=max_attempts,
            dedupe_key=dedupe_key,
        )
        self.db.add(job)
        try:
            async with self.db.begin_nested():
                await self.db.flush()
        except IntegrityError:
            if not dedupe_key:
                raise
            self.db.expunge(job)
            existing = await self.db.scalar(
                select(BackgroundJob).where(BackgroundJob.dedupe_key == dedupe_key)
            )
            if existing is None:  # pragma: no cover - constraint violated by something else
                raise
            return existing
        await self.db.refresh(job)
        return job

    # ------------------------------------------------------------------
    # Claiming
    # ------------------------------------------------------------------

    async def _candidate_ids(
        self, now: datetime, limit: int, organization_id: UUID | None = None
    ) -> list[UUID]:
        due = or_(BackgroundJob.run_after.is_(None), BackgroundJob.run_after <= now)
        ready = or_(
            # Fresh work.
            BackgroundJob.status.in_((JobStatus.queued, JobStatus.retrying)) & due,
            # Abandoned work whose lease has lapsed.
            (BackgroundJob.status == JobStatus.running)
            & BackgroundJob.lease_expires_at.isnot(None)
            & (BackgroundJob.lease_expires_at <= now),
        )
        stmt = (
            select(BackgroundJob.id)
            .where(ready)
            .order_by(BackgroundJob.created_at.asc())
            .limit(limit)
        )
        if organization_id is not None:
            stmt = stmt.where(BackgroundJob.organization_id == organization_id)
        if self.db.bind is not None and self.db.bind.dialect.name == "postgresql":
            stmt = stmt.with_for_update(skip_locked=True)
        return list((await self.db.execute(stmt)).scalars().all())

    async def claim(
        self,
        job_id: UUID,
        *,
        now: datetime | None = None,
        organization_id: UUID | None = None,
    ) -> BackgroundJob | None:
        """
        Atomically take ownership of a job.

        Returns the job when this worker won the race, otherwise None. The
        WHERE clause re-checks the status and lease, so a second worker
        attempting the same job updates zero rows.

        `organization_id` narrows the same UPDATE, so a request-scoped caller
        cannot claim another tenant's job even if it learns the id.
        """
        now = now or datetime.now(timezone.utc)
        lease_until = now + timedelta(seconds=self.lease_seconds)
        due = or_(BackgroundJob.run_after.is_(None), BackgroundJob.run_after <= now)
        claimable = or_(
            BackgroundJob.status.in_((JobStatus.queued, JobStatus.retrying)) & due,
            (BackgroundJob.status == JobStatus.running)
            & BackgroundJob.lease_expires_at.isnot(None)
            & (BackgroundJob.lease_expires_at <= now),
        )
        owned = (
            BackgroundJob.organization_id == organization_id
            if organization_id is not None
            else True_()
        )

        result = await self.db.execute(
            update(BackgroundJob)
            .where(BackgroundJob.id == job_id, claimable, owned)
            .values(
                status=JobStatus.running,
                locked_by=self.worker_id,
                lease_expires_at=lease_until,
                heartbeat_at=now,
                started_at=now,
                attempts=BackgroundJob.attempts + 1,
            )
            .execution_options(synchronize_session=False)
        )
        if result.rowcount != 1:
            return None

        await self.db.flush()
        job = await self.db.get(BackgroundJob, job_id)
        if job is not None:
            await self.db.refresh(job)
        return job

    async def heartbeat(self, job: BackgroundJob) -> bool:
        """Extend the lease of a job this worker still owns."""
        now = datetime.now(timezone.utc)
        result = await self.db.execute(
            update(BackgroundJob)
            .where(
                BackgroundJob.id == job.id,
                BackgroundJob.locked_by == self.worker_id,
                BackgroundJob.status == JobStatus.running,
            )
            .values(heartbeat_at=now, lease_expires_at=now + timedelta(seconds=self.lease_seconds))
            .execution_options(synchronize_session=False)
        )
        await self.db.flush()
        return result.rowcount == 1

    # ------------------------------------------------------------------
    # Processing
    # ------------------------------------------------------------------

    async def process_due(
        self, limit: int = 10, *, organization_id: UUID | None = None
    ) -> list[BackgroundJob]:
        """
        Claim and run due jobs.

        Unscoped is the worker's mode. Any caller reachable from an HTTP request
        must pass `organization_id`; without it this drains the whole queue,
        which across tenants is a privilege escalation, not a performance win.
        """
        now = datetime.now(timezone.utc)
        processed: list[BackgroundJob] = []

        for job_id in await self._candidate_ids(now, limit, organization_id):
            job = await self.claim(job_id, now=now, organization_id=organization_id)
            if job is None:
                # Another worker got there first. Not an error.
                continue
            processed.append(await self._run_claimed(job))
        return processed

    async def _run_claimed(self, job: BackgroundJob) -> BackgroundJob:
        handler = self._handlers.get(job.job_type)
        if handler is None:
            return await self._fail(job, f"No handler for job_type={job.job_type}", retryable=False)
        try:
            result = await handler(self.db, job)
        except Exception as exc:
            return await self._fail(job, str(exc), retryable=True)
        return await self._complete(job, result)

    async def _complete(self, job: BackgroundJob, result: dict) -> BackgroundJob:
        job.result = result
        job.status = JobStatus.completed
        job.finished_at = datetime.now(timezone.utc)
        job.error = None
        self._release_lease(job)
        await self.db.flush()
        return job

    async def _fail(self, job: BackgroundJob, error: str, *, retryable: bool) -> BackgroundJob:
        job.error = error
        now = datetime.now(timezone.utc)
        if not retryable or job.attempts >= job.max_attempts:
            job.status = JobStatus.failed
            job.finished_at = now
        else:
            # Exponential-ish backoff, capped.
            delay = min(2**job.attempts, MAX_BACKOFF_MINUTES)
            job.status = JobStatus.retrying
            job.run_after = now + timedelta(minutes=delay)
            job.finished_at = None
        self._release_lease(job)
        await self.db.flush()
        return job

    @staticmethod
    def _release_lease(job: BackgroundJob) -> None:
        job.locked_by = None
        job.lease_expires_at = None

    # ------------------------------------------------------------------
    # Recovery / administration
    # ------------------------------------------------------------------

    async def reap_expired_leases(self) -> int:
        """
        Move jobs abandoned by dead workers back into a runnable state.

        Jobs that already exhausted their attempts are failed explicitly rather
        than left in `running` where they would look like work in progress.
        """
        now = datetime.now(timezone.utc)
        expired = (
            (
                await self.db.execute(
                    select(BackgroundJob).where(
                        BackgroundJob.status == JobStatus.running,
                        BackgroundJob.lease_expires_at.isnot(None),
                        BackgroundJob.lease_expires_at <= now,
                    )
                )
            )
            .scalars()
            .all()
        )
        for job in expired:
            if job.attempts >= job.max_attempts:
                job.status = JobStatus.failed
                job.error = (job.error or "") + " Worker lease expired; attempts exhausted."
                job.finished_at = now
            else:
                job.status = JobStatus.retrying
                job.run_after = now
                job.error = (job.error or "") + " Worker lease expired; requeued."
            self._release_lease(job)
        await self.db.flush()
        return len(expired)

    async def retry(
        self,
        job_id: UUID,
        *,
        reset_attempts: bool = False,
        organization_id: UUID | None = None,
    ) -> BackgroundJob | None:
        """Make a failed job runnable again."""
        job = await self._owned(job_id, organization_id)
        if job is None or job.status != JobStatus.failed:
            return None
        job.status = JobStatus.queued
        job.run_after = None
        job.finished_at = None
        job.error = None
        if reset_attempts:
            job.attempts = 0
        self._release_lease(job)
        await self.db.flush()
        return job

    async def _owned(self, job_id: UUID, organization_id: UUID | None) -> BackgroundJob | None:
        """
        Load a job, honouring tenant ownership.

        Repeated here rather than left to the route: the queue is the layer that
        mutates job state, so it is the layer that has to refuse.
        """
        job = await self.db.get(BackgroundJob, job_id)
        if job is None:
            return None
        if organization_id is not None and job.organization_id != organization_id:
            return None
        return job

    async def cancel(
        self, job_id: UUID, *, organization_id: UUID | None = None
    ) -> BackgroundJob | None:
        """Cancel a job that has not started or is between attempts."""
        job = await self._owned(job_id, organization_id)
        if job is None or job.status not in (JobStatus.queued, JobStatus.retrying):
            return None
        job.status = JobStatus.cancelled
        job.finished_at = datetime.now(timezone.utc)
        self._release_lease(job)
        await self.db.flush()
        return job
