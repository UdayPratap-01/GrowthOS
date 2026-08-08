from __future__ import annotations

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.jobs.queue import JobQueue
from app.models.automation import BackgroundJob, ScheduledPost
from app.models.enums import JobStatus
from app.publishing import get_publisher
from sqlalchemy import select
from datetime import datetime, timezone


async def handle_publish_due(db: AsyncSession, job: BackgroundJob) -> dict:
    """Attempt due scheduled posts — only succeed with platform confirmation or explicit DEMO."""
    now = datetime.now(timezone.utc)
    rows = (
        await db.execute(
            select(ScheduledPost).where(
                ScheduledPost.organization_id == job.organization_id,
                ScheduledPost.status == "scheduled",
                ScheduledPost.scheduled_for <= now,
            ).limit(20)
        )
    ).scalars().all()
    results = []
    for row in rows:
        publisher = get_publisher(db, row.platform)
        if not publisher:
            row.status = "failed"
            row.error = "INTEGRATION NOT CONNECTED"
            results.append({"id": str(row.id), "error": row.error})
            continue
        pub = await publisher.publish(
            content=row.content,
            organization_id=row.organization_id,
            client_id=row.client_id,
        )
        if pub.demo:
            row.status = "demo_published"
            row.publish_result = {"note": "DEMO DATA", **pub.platform_response}
            results.append({"id": str(row.id), "demo": True})
        elif pub.success and pub.external_id:
            row.status = "published"
            row.external_id = pub.external_id
            row.publish_result = pub.platform_response
            results.append({"id": str(row.id), "external_id": pub.external_id})
        else:
            row.status = "failed"
            row.error = pub.error or pub.message
            results.append({"id": str(row.id), "error": row.error})
    await db.flush()
    return {"published_attempts": results}


async def process_organization_jobs(db: AsyncSession, organization_id: UUID) -> list[BackgroundJob]:
    queue = JobQueue(db)
    queue.register("publish_due", handle_publish_due)
    # Ensure a publish_due job exists
    existing = await db.scalar(
        select(BackgroundJob).where(
            BackgroundJob.organization_id == organization_id,
            BackgroundJob.job_type == "publish_due",
            BackgroundJob.status == JobStatus.queued,
        ).limit(1)
    )
    if not existing:
        await queue.enqueue(job_type="publish_due", payload={}, organization_id=organization_id)
    return await queue.process_due(limit=10)
