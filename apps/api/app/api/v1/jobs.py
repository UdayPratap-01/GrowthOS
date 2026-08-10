"""
Background job status API.

Long operations return a job id immediately; the frontend polls here. Every
query is scoped to the caller's organization, so one tenant cannot read or
cancel another tenant's work.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import AuthContext, get_current_auth
from app.core.permissions import Permission, require_permission
from app.db.session import get_db
from app.models.automation import BackgroundJob
from app.models.enums import JobStatus
from app.schemas.jobs import JobOut

router = APIRouter(prefix="/jobs", tags=["jobs"])

#: Statuses that mean "no longer changing", so a client can stop polling.
TERMINAL = {JobStatus.completed, JobStatus.failed, JobStatus.cancelled}


def _to_out(job: BackgroundJob) -> JobOut:
    return JobOut(
        id=job.id,
        job_type=job.job_type,
        status=job.status.value.upper(),
        attempts=job.attempts,
        max_attempts=job.max_attempts,
        error=job.error,
        result=job.result or {},
        run_after=job.run_after,
        started_at=job.started_at,
        finished_at=job.finished_at,
        created_at=job.created_at,
        terminal=job.status in TERMINAL,
    )


async def _load(db: AsyncSession, organization_id: UUID, job_id: UUID) -> BackgroundJob:
    job = await db.scalar(
        select(BackgroundJob).where(
            BackgroundJob.id == job_id,
            BackgroundJob.organization_id == organization_id,
        )
    )
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="JOB_NOT_FOUND")
    return job


@router.get("", response_model=list[JobOut])
async def list_jobs(
    job_type: str | None = Query(default=None),
    job_status: str | None = Query(default=None, alias="status"),
    limit: int = Query(default=50, ge=1, le=200),
    auth: AuthContext = Depends(get_current_auth),
    db: AsyncSession = Depends(get_db),
) -> list[JobOut]:
    stmt = (
        select(BackgroundJob)
        .where(BackgroundJob.organization_id == auth.organization_id)
        .order_by(BackgroundJob.created_at.desc())
        .limit(limit)
    )
    if job_type:
        stmt = stmt.where(BackgroundJob.job_type == job_type)
    if job_status:
        try:
            stmt = stmt.where(BackgroundJob.status == JobStatus(job_status.lower()))
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Unknown status {job_status!r}",
            ) from exc
    rows = (await db.execute(stmt)).scalars().all()
    return [_to_out(row) for row in rows]


@router.get("/{job_id}", response_model=JobOut)
async def get_job(
    job_id: UUID,
    auth: AuthContext = Depends(get_current_auth),
    db: AsyncSession = Depends(get_db),
) -> JobOut:
    return _to_out(await _load(db, auth.organization_id, job_id))


@router.post("/{job_id}/retry", response_model=JobOut)
async def retry_job(
    job_id: UUID,
    auth: AuthContext = Depends(require_permission(Permission.content_write)),
    db: AsyncSession = Depends(get_db),
) -> JobOut:
    from app.jobs.queue import JobQueue

    job = await _load(db, auth.organization_id, job_id)
    retried = await JobQueue(db).retry(
        job.id, reset_attempts=True, organization_id=auth.organization_id
    )
    if retried is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Only failed jobs can be retried; this job is {job.status.value.upper()}.",
        )
    await db.commit()
    return _to_out(retried)


@router.post("/{job_id}/cancel", response_model=JobOut)
async def cancel_job(
    job_id: UUID,
    auth: AuthContext = Depends(require_permission(Permission.content_write)),
    db: AsyncSession = Depends(get_db),
) -> JobOut:
    from app.jobs.queue import JobQueue

    job = await _load(db, auth.organization_id, job_id)
    cancelled = await JobQueue(db).cancel(job.id, organization_id=auth.organization_id)
    if cancelled is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "Only queued or retrying jobs can be cancelled; this job is "
                f"{job.status.value.upper()}."
            ),
        )
    await db.commit()
    return _to_out(cancelled)
