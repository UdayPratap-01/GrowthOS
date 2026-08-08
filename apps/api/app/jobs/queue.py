"""Background job abstraction — DB-backed queue (Redis/Celery-compatible later)."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Awaitable, Callable
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.automation import BackgroundJob
from app.models.enums import JobStatus

JobHandler = Callable[[AsyncSession, BackgroundJob], Awaitable[dict]]


class JobQueue:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db
        self._handlers: dict[str, JobHandler] = {}

    def register(self, job_type: str, handler: JobHandler) -> None:
        self._handlers[job_type] = handler

    async def enqueue(
        self,
        *,
        job_type: str,
        payload: dict,
        organization_id: UUID | None = None,
        run_after: datetime | None = None,
        max_attempts: int = 3,
    ) -> BackgroundJob:
        job = BackgroundJob(
            organization_id=organization_id,
            job_type=job_type,
            status=JobStatus.queued,
            payload=payload,
            run_after=run_after,
            max_attempts=max_attempts,
        )
        self.db.add(job)
        await self.db.flush()
        await self.db.refresh(job)
        return job

    async def process_due(self, limit: int = 10) -> list[BackgroundJob]:
        now = datetime.now(timezone.utc)
        rows = (
            await self.db.execute(
                select(BackgroundJob)
                .where(
                    BackgroundJob.status == JobStatus.queued,
                    (BackgroundJob.run_after.is_(None)) | (BackgroundJob.run_after <= now),
                )
                .order_by(BackgroundJob.created_at.asc())
                .limit(limit)
            )
        ).scalars().all()
        processed: list[BackgroundJob] = []
        for job in rows:
            handler = self._handlers.get(job.job_type)
            job.status = JobStatus.running
            job.started_at = now
            job.attempts = int(job.attempts or 0) + 1
            await self.db.flush()
            if not handler:
                job.status = JobStatus.failed
                job.error = f"No handler for job_type={job.job_type}"
                job.finished_at = datetime.now(timezone.utc)
                processed.append(job)
                continue
            try:
                result = await handler(self.db, job)
                job.result = result
                job.status = JobStatus.completed
                job.finished_at = datetime.now(timezone.utc)
            except Exception as exc:
                job.error = str(exc)
                if job.attempts >= job.max_attempts:
                    job.status = JobStatus.failed
                    job.finished_at = datetime.now(timezone.utc)
                else:
                    # exponential-ish backoff via run_after minutes = 2^attempts
                    from datetime import timedelta

                    delay = min(2 ** job.attempts, 60)
                    job.status = JobStatus.queued
                    job.run_after = datetime.now(timezone.utc) + timedelta(minutes=delay)
            processed.append(job)
            await self.db.flush()
        return processed
