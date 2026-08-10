"""
Job handlers.

Each handler receives an `AsyncSession` and the claimed `BackgroundJob`, and
returns a JSON-serialisable result. Raising marks the job for retry with
backoff; returning normally completes it. A handler must therefore only raise
for conditions worth retrying — a permanently unusable input should be recorded
in the result instead.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.jobs.queue import JobQueue
from app.models.automation import BackgroundJob, ImageJob, ScheduledPost, VideoJob
from app.models.enums import JobStatus
from app.models.organization import Organization
from app.publishing import get_publisher

logger = logging.getLogger(__name__)


class UnrecoverableJobError(Exception):
    """The job can never succeed; do not burn retries on it."""


async def _load_organization(db: AsyncSession, organization_id) -> Organization:
    org = await db.get(Organization, organization_id)
    if org is None:
        raise UnrecoverableJobError(f"Organization {organization_id} no longer exists")
    return org


def _assert_same_tenant(job: BackgroundJob, entity, label: str) -> None:
    """
    Refuse to act on a record belonging to a different tenant than the job.

    The payload carries an id, and an id is guessable. Binding the referenced
    record back to the job's own organization means a forged or mistaken payload
    cannot make a worker touch another tenant's data.
    """
    owner = getattr(entity, "organization_id", None)
    if job.organization_id is not None and owner is not None and owner != job.organization_id:
        raise UnrecoverableJobError(
            f"{label} does not belong to organization {job.organization_id}"
        )


# --------------------------------------------------------------------------
# Media
# --------------------------------------------------------------------------


async def handle_generate_image(db: AsyncSession, job: BackgroundJob) -> dict:
    """Run one image generation. The HTTP request only enqueued this."""
    from app.services.media_generation_service import MediaGenerationService

    image_job_id = (job.payload or {}).get("image_job_id")
    if not image_job_id:
        raise UnrecoverableJobError("payload.image_job_id is required")

    image_job = await db.get(ImageJob, UUID(str(image_job_id)))
    if image_job is None:
        raise UnrecoverableJobError(f"ImageJob {image_job_id} not found")
    _assert_same_tenant(job, image_job, f"ImageJob {image_job_id}")
    if image_job.status == JobStatus.completed:
        # A duplicate delivery, not a problem: report the existing outcome.
        return {"image_job_id": str(image_job.id), "status": "COMPLETED", "duplicate": True}

    organization = await _load_organization(db, image_job.organization_id)
    service = MediaGenerationService(db)
    await service._process_image_job(
        organization, image_job, platform=(job.payload or {}).get("platform")
    )
    await db.flush()

    if image_job.status == JobStatus.failed:
        # Surface the reason on the background job too, so an operator looking at
        # the queue sees why without joining to image_jobs.
        return {
            "image_job_id": str(image_job.id),
            "status": "FAILED",
            "error": image_job.error,
            "error_code": image_job.error_code,
            "retryable": bool(image_job.retryable),
        }
    return {
        "image_job_id": str(image_job.id),
        "status": image_job.status.value.upper(),
        "creative_asset_id": str(image_job.creative_asset_id) if image_job.creative_asset_id else None,
    }


async def handle_generate_video(db: AsyncSession, job: BackgroundJob) -> dict:
    """
    Submit a video to the provider.

    Submission and completion are separate jobs. Video generation takes minutes,
    so holding a worker (or, previously, an HTTP request) open while polling
    wastes the slot and loses everything if the process restarts. This handler
    submits and hands off to `media.poll_video`.
    """
    from app.jobs.registry import VIDEO_POLL
    from app.services.media_generation_service import MediaGenerationService

    video_job_id = (job.payload or {}).get("video_job_id")
    if not video_job_id:
        raise UnrecoverableJobError("payload.video_job_id is required")

    video_job = await db.get(VideoJob, UUID(str(video_job_id)))
    if video_job is None:
        raise UnrecoverableJobError(f"VideoJob {video_job_id} not found")
    _assert_same_tenant(job, video_job, f"VideoJob {video_job_id}")
    if video_job.status == JobStatus.completed:
        return {"video_job_id": str(video_job.id), "status": "COMPLETED", "duplicate": True}

    organization = await _load_organization(db, video_job.organization_id)
    service = MediaGenerationService(db)
    await service._process_video_job(
        organization, video_job, platform=(job.payload or {}).get("platform"), poll=False
    )
    await db.flush()

    if video_job.status in {JobStatus.submitted, JobStatus.processing, JobStatus.generating}:
        await JobQueue(db).enqueue(
            job_type=VIDEO_POLL,
            payload={
                "video_job_id": str(video_job.id),
                "platform": (job.payload or {}).get("platform"),
                "deadline": _video_deadline().isoformat(),
            },
            organization_id=video_job.organization_id,
            run_after=datetime.now(timezone.utc) + timedelta(seconds=15),
            max_attempts=1,
            dedupe_key=f"video-poll:{video_job.id}",
        )
    return {"video_job_id": str(video_job.id), "status": video_job.status.value.upper()}


async def handle_poll_video(db: AsyncSession, job: BackgroundJob) -> dict:
    """
    Ask the provider whether a submitted video is ready.

    Reschedules itself until the job finishes or the deadline passes. Each poll
    is its own short-lived job, so a worker restart resumes rather than orphans.
    """
    from app.jobs.registry import VIDEO_POLL
    from app.services.media_generation_service import MediaGenerationService

    payload = job.payload or {}
    video_job_id = payload.get("video_job_id")
    if not video_job_id:
        raise UnrecoverableJobError("payload.video_job_id is required")

    video_job = await db.get(VideoJob, UUID(str(video_job_id)))
    if video_job is None:
        raise UnrecoverableJobError(f"VideoJob {video_job_id} not found")
    _assert_same_tenant(job, video_job, f"VideoJob {video_job_id}")
    if video_job.status in {JobStatus.completed, JobStatus.failed, JobStatus.cancelled}:
        return {"video_job_id": str(video_job.id), "status": video_job.status.value.upper()}

    organization = await _load_organization(db, video_job.organization_id)
    await MediaGenerationService(db)._process_video_job(
        organization, video_job, platform=payload.get("platform"), poll=True
    )
    await db.flush()

    still_running = video_job.status in {
        JobStatus.submitted,
        JobStatus.processing,
        JobStatus.generating,
    }
    if not still_running:
        return {"video_job_id": str(video_job.id), "status": video_job.status.value.upper()}

    deadline = _parse_deadline(payload.get("deadline"))
    now = datetime.now(timezone.utc)
    if now >= deadline:
        # Do not leave the job looking like work in progress forever.
        video_job.status = JobStatus.failed
        video_job.error = "PROVIDER_TIMEOUT: video was still processing at the deadline"
        video_job.error_code = "PROVIDER_TIMEOUT"
        video_job.retryable = True
        await db.flush()
        return {"video_job_id": str(video_job.id), "status": "FAILED", "error": video_job.error}

    polls = int(payload.get("polls") or 0) + 1
    await JobQueue(db).enqueue(
        job_type=VIDEO_POLL,
        payload={**payload, "polls": polls},
        organization_id=video_job.organization_id,
        run_after=now + timedelta(seconds=min(15 * polls, 60)),
        max_attempts=1,
        dedupe_key=f"video-poll:{video_job.id}:{polls}",
    )
    return {"video_job_id": str(video_job.id), "status": "PROCESSING", "polls": polls}


def _video_deadline() -> datetime:
    from app.core.config import get_settings

    return datetime.now(timezone.utc) + timedelta(
        seconds=get_settings().video_job_timeout_seconds
    )


def _parse_deadline(value) -> datetime:
    if not value:
        return _video_deadline()
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError:
        return _video_deadline()
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


# --------------------------------------------------------------------------
# Campaign generation
# --------------------------------------------------------------------------


async def handle_generate_campaign(db: AsyncSession, job: BackgroundJob) -> dict:
    """
    Run one P2-A campaign generation.

    The HTTP request only recorded the run and enqueued this. Failures are
    recorded on the run itself by the service, so this handler returns normally
    for a failed generation rather than raising: retrying a campaign whose
    strategy call was rejected would spend the same money again for the same
    result, and the reviewer already has the reason.
    """
    from app.models.creative import CampaignGenerationRun
    from app.services.campaign_generation_service import CampaignGenerationService

    run_id = (job.payload or {}).get("run_id")
    if not run_id:
        raise UnrecoverableJobError("payload.run_id is required")

    run = await db.get(CampaignGenerationRun, UUID(str(run_id)))
    if run is None:
        raise UnrecoverableJobError(f"CampaignGenerationRun {run_id} not found")
    _assert_same_tenant(job, run, f"CampaignGenerationRun {run_id}")

    organization = await _load_organization(db, run.organization_id)
    service = CampaignGenerationService(db)
    await service.execute(organization, run)
    await service.schedule_reconcile(run)
    await db.flush()
    return {
        "run_id": str(run.id),
        "status": run.status,
        "campaign_id": str(run.campaign_id) if run.campaign_id else None,
        "error_code": run.error_code,
    }


async def handle_reconcile_campaign_run(db: AsyncSession, job: BackgroundJob) -> dict:
    """
    Bring a run's media counts up to date and promote it when the media is done.

    Reschedules itself while anything is outstanding, in the same shape as
    `media.poll_video`, so a run reaches READY_FOR_REVIEW even if nobody is
    polling it from the UI.
    """
    from app.models.creative import CampaignGenerationRun
    from app.services.campaign_generation_service import CampaignGenerationService

    run_id = (job.payload or {}).get("run_id")
    if not run_id:
        raise UnrecoverableJobError("payload.run_id is required")

    run = await db.get(CampaignGenerationRun, UUID(str(run_id)))
    if run is None:
        raise UnrecoverableJobError(f"CampaignGenerationRun {run_id} not found")
    _assert_same_tenant(job, run, f"CampaignGenerationRun {run_id}")

    service = CampaignGenerationService(db)
    await service.reconcile(run)
    await service.schedule_reconcile(run)
    await db.flush()
    return {"run_id": str(run.id), "status": run.status}


# --------------------------------------------------------------------------
# Reports and analytics
# --------------------------------------------------------------------------


async def handle_generate_report(db: AsyncSession, job: BackgroundJob) -> dict:
    from app.services.report_service import ReportService

    payload = job.payload or {}
    client_id = payload.get("client_id")
    if not client_id:
        raise UnrecoverableJobError("payload.client_id is required")

    organization = await _load_organization(db, job.organization_id)
    report = await ReportService(db).generate(
        organization,
        UUID(str(payload["user_id"])) if payload.get("user_id") else None,
        UUID(str(client_id)),
        period_days=int(payload.get("period_days") or 7),
    )
    await db.flush()
    return {"report_id": str(report.id), "export_available": bool(report.export_path)}


async def handle_analytics_sync(db: AsyncSession, job: BackgroundJob) -> dict:
    """
    Pull fresh analytics for one provider.

    A provider that is not connected is reported as such — never simulated, and
    never retried, because reconnecting is a user action.
    """
    from app.services.integration_service import IntegrationService

    payload = job.payload or {}
    provider = payload.get("provider")
    if not provider:
        raise UnrecoverableJobError("payload.provider is required")

    organization = await _load_organization(db, job.organization_id)
    client_id = UUID(str(payload["client_id"])) if payload.get("client_id") else None
    result = await IntegrationService(db).sync(
        str(provider), organization_id=organization.id, client_id=client_id
    )
    await db.flush()
    return {
        "provider": provider,
        "client_id": str(client_id) if client_id else None,
        "success": bool(getattr(result, "success", False)),
        "message": getattr(result, "message", None),
        "records": getattr(result, "records_synced", None),
    }


async def handle_lead_backfill(db: AsyncSession, job: BackgroundJob) -> dict:
    """Retry contact-detail retrieval for a Meta lead. See P1-7."""
    from app.services.lead_backfill_service import backfill_lead_contact

    lead_id = (job.payload or {}).get("lead_id")
    if not lead_id:
        raise UnrecoverableJobError("payload.lead_id is required")
    # The lead must belong to the job's tenant; the service treats a mismatch as
    # a non-existent lead rather than confirming that someone else's lead exists.
    return await backfill_lead_contact(
        db, UUID(str(lead_id)), organization_id=job.organization_id
    )


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


async def enqueue_publish_due(db: AsyncSession, organization_id: UUID) -> BackgroundJob:
    """
    Ensure this organization has a `publish_due` job waiting for the worker.

    In-flight covers queued, awaiting-retry and actively running work, so a poll
    never stacks a duplicate job on top of one already going.
    """
    from app.jobs.registry import build_queue

    existing = await db.scalar(
        select(BackgroundJob).where(
            BackgroundJob.organization_id == organization_id,
            BackgroundJob.job_type == "publish_due",
            BackgroundJob.status.in_((JobStatus.queued, JobStatus.retrying, JobStatus.running)),
        ).limit(1)
    )
    if existing is not None:
        return existing
    return await build_queue(db).enqueue(
        job_type="publish_due", payload={}, organization_id=organization_id
    )


async def process_organization_jobs(db: AsyncSession, organization_id: UUID) -> list[BackgroundJob]:
    """
    Run this organization's due jobs and nothing else.

    `organization_id` is passed into the claim predicate rather than filtered
    afterwards: an unscoped drain here would let any authenticated user execute
    another tenant's work using that tenant's integrations and credentials.
    """
    from app.jobs.registry import build_queue

    await enqueue_publish_due(db, organization_id)
    return await build_queue(db).process_due(limit=10, organization_id=organization_id)
