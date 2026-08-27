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


async def handle_analytics_ingest(db: AsyncSession, job: BackgroundJob) -> dict:
    """
    Normalized Meta/Google Ads performance ingestion into marketing_performance_daily.

    Retryable provider failures (timeout, transport, rate limit) raise so the
    JobQueue backoff path can retry. Permanent config/auth failures are
    unrecoverable and must not burn attempts.
    """
    from app.analytics.errors import AnalyticsIngestionError
    from app.analytics.ingestion import AnalyticsIngestionService

    payload = job.payload or {}
    provider = payload.get("provider")
    if not provider:
        raise UnrecoverableJobError("payload.provider is required")
    if job.organization_id is None:
        raise UnrecoverableJobError("analytics.ingest requires organization_id")

    client_id = UUID(str(payload["client_id"])) if payload.get("client_id") else None
    if client_id is not None:
        from app.models.client import Client

        client = await db.get(Client, client_id)
        if client is None:
            raise UnrecoverableJobError(f"Client {client_id} not found")
        _assert_same_tenant(job, client, f"Client {client_id}")

    actor_raw = payload.get("actor_user_id")
    actor_user_id = UUID(str(actor_raw)) if actor_raw else None
    lookback = int(payload.get("lookback_days") or 7)
    entity_level = str(payload.get("entity_level") or "campaign")

    try:
        return await AnalyticsIngestionService(db).ingest(
            organization_id=job.organization_id,
            provider=str(provider),
            client_id=client_id,
            lookback_days=lookback,
            entity_level=entity_level,
            actor_user_id=actor_user_id,
            trigger="job",
        )
    except AnalyticsIngestionError as exc:
        if not exc.retryable:
            raise UnrecoverableJobError(f"{exc.code}: {exc.message}") from exc
        raise


async def handle_analytics_analyze(db: AsyncSession, job: BackgroundJob) -> dict:
    """
    Deterministic performance intelligence over MarketingPerformanceDaily.

    Analysis-only: never creates AIAction rows and never mutates ad platforms.
    """
    from app.analytics.intelligence import PerformanceIntelligenceService

    if job.organization_id is None:
        raise UnrecoverableJobError("analytics.analyze requires organization_id")

    payload = job.payload or {}
    client_id = UUID(str(payload["client_id"])) if payload.get("client_id") else None
    if client_id is not None:
        from app.models.client import Client

        client = await db.get(Client, client_id)
        if client is None:
            raise UnrecoverableJobError(f"Client {client_id} not found")
        _assert_same_tenant(job, client, f"Client {client_id}")

    actor_raw = payload.get("actor_user_id")
    actor_user_id = UUID(str(actor_raw)) if actor_raw else None
    window_days = int(payload.get("window_days") or 7)
    if window_days not in {7, 14, 30}:
        raise UnrecoverableJobError("window_days must be 7, 14, or 30")

    try:
        return await PerformanceIntelligenceService(db).analyze(
            organization_id=job.organization_id,
            client_id=client_id,
            window_days=window_days,
            platform=payload.get("platform"),
            entity_level=str(payload.get("entity_level") or "campaign"),
            actor_user_id=actor_user_id,
            use_ai_explanation=bool(payload.get("use_ai_explanation", True)),
            trigger="job",
        )
    except ValueError as exc:
        raise UnrecoverableJobError(str(exc)) from exc



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


# --------------------------------------------------------------------------
# Scheduled autopilot
# --------------------------------------------------------------------------


async def handle_autopilot_scheduler_tick(db: AsyncSession, job: BackgroundJob) -> dict:
    """
    Discover automation-enabled tenants and enqueue bounded autopilot cycle jobs.

    Does not execute cycles itself — only enqueues work for the existing worker.
    """
    from app.core.config import get_settings
    from app.jobs.autopilot_scheduler import (
        discover_autopilot_targets,
        enqueue_autopilot_cycle,
        schedule_next_scheduler_tick,
        scheduled_window_start,
    )
    from app.security.audit import write_audit

    settings = get_settings()
    if not settings.autopilot_scheduler_enabled:
        logger.info("autopilot scheduler tick skipped scheduler_disabled job_id=%s", job.id)
        return {"skipped": True, "reason": "SCHEDULER_DISABLED"}

    payload = job.payload or {}
    now = datetime.now(timezone.utc)
    window_raw = payload.get("window")
    if window_raw:
        try:
            window = datetime.fromisoformat(str(window_raw))
            if window.tzinfo is None:
                window = window.replace(tzinfo=timezone.utc)
        except ValueError:
            window = scheduled_window_start(now, settings.autopilot_interval_minutes)
    else:
        window = scheduled_window_start(now, settings.autopilot_interval_minutes)

    logger.info(
        "autopilot scheduler tick started job_id=%s window=%s max_orgs=%d",
        job.id,
        window.isoformat(),
        settings.autopilot_max_orgs_per_cycle,
    )

    targets = await discover_autopilot_targets(
        db, max_orgs=settings.autopilot_max_orgs_per_cycle
    )
    enqueued = 0
    skipped = 0
    failures = 0
    enqueued_jobs: list[str] = []

    for organization, client in targets:
        try:
            result = await enqueue_autopilot_cycle(
                db,
                organization=organization,
                client=client,
                window_start=window,
            )
            if result.skipped:
                skipped += 1
                logger.info(
                    "autopilot cycle skipped org=%s client=%s reason=%s",
                    organization.id,
                    client.id,
                    result.reason,
                )
            else:
                enqueued += 1
                if result.job is not None:
                    enqueued_jobs.append(str(result.job.id))
        except Exception:
            failures += 1
            logger.exception(
                "autopilot cycle enqueue failed org=%s client=%s",
                organization.id,
                client.id,
            )

    next_job = await schedule_next_scheduler_tick(db)
    await write_audit(
        db,
        action="autopilot.scheduler.tick",
        organization_id=None,
        user_id=None,
        resource_type="background_job",
        resource_id=str(job.id),
        details={
            "window": window.isoformat(),
            "discovered": len(targets),
            "enqueued": enqueued,
            "skipped": skipped,
            "failures": failures,
            "next_tick_job_id": str(next_job.id) if next_job else None,
        },
    )
    await db.flush()

    logger.info(
        "autopilot scheduler tick completed job_id=%s discovered=%d enqueued=%d skipped=%d failures=%d",
        job.id,
        len(targets),
        enqueued,
        skipped,
        failures,
    )
    return {
        "window": window.isoformat(),
        "discovered": len(targets),
        "enqueued": enqueued,
        "skipped": skipped,
        "failures": failures,
        "enqueued_job_ids": enqueued_jobs,
        "next_tick_job_id": str(next_job.id) if next_job else None,
    }


async def handle_autopilot_cycle(db: AsyncSession, job: BackgroundJob) -> dict:
    """Run one bounded autopilot cycle via AutopilotOrchestratorService."""
    from app.jobs.autopilot_scheduler import SCHEDULER_ACTOR_USER_ID
    from app.models.client import Client
    from app.security.audit import write_audit
    from app.services.autonomy_service import AutonomyService
    from app.services.autopilot_orchestrator_service import AutopilotOrchestratorService

    if job.organization_id is None:
        raise UnrecoverableJobError("autopilot.cycle requires organization_id")

    payload = job.payload or {}
    client_id_raw = payload.get("client_id")
    if not client_id_raw:
        raise UnrecoverableJobError("payload.client_id is required")

    client_id = UUID(str(client_id_raw))
    client = await db.get(Client, client_id)
    if client is None:
        raise UnrecoverableJobError(f"Client {client_id} not found")
    _assert_same_tenant(job, client, f"Client {client_id}")

    organization = await _load_organization(db, job.organization_id)
    settings = await AutonomyService(db).get_effective(organization.id, client_id)
    if not settings.automation_enabled:
        logger.info(
            "autopilot cycle skipped automation_disabled org=%s client=%s job_id=%s",
            organization.id,
            client_id,
            job.id,
        )
        return {"skipped": True, "reason": "AUTOMATION_DISABLED"}

    max_iterations = min(int(payload.get("max_iterations") or 1), settings.max_ai_iterations or 1)
    run_id = UUID(str(payload["run_id"])) if payload.get("run_id") else None

    logger.info(
        "autopilot cycle started org=%s client=%s job_id=%s demo=%s",
        organization.id,
        client_id,
        job.id,
        organization.demo_mode,
    )

    result = await AutopilotOrchestratorService(db).run_cycle(
        organization,
        client_id=client_id,
        run_id=run_id,
        user_id=SCHEDULER_ACTOR_USER_ID,
        max_iterations=max_iterations,
    )

    await write_audit(
        db,
        action="autopilot.cycle.scheduled",
        organization_id=organization.id,
        user_id=None,
        resource_type="background_job",
        resource_id=str(job.id),
        details={
            "client_id": str(client_id),
            "cycle_id": result.cycle_id,
            "actions_created": result.actions_created,
            "actions_blocked": result.actions_blocked,
            "trigger": payload.get("trigger") or "scheduler",
            "demo_mode": organization.demo_mode,
            "errors": result.errors[:5],
        },
    )
    await db.flush()

    logger.info(
        "autopilot cycle completed org=%s client=%s job_id=%s actions_created=%d",
        organization.id,
        client_id,
        job.id,
        result.actions_created,
    )
    return result.model_dump(mode="json")


async def handle_provider_reconcile(db: AsyncSession, job: BackgroundJob) -> dict:
    """Read-only provider status lookup for an ambiguous ads mutation."""
    from app.automation.provider_reconciliation import reconcile_action

    action_id_raw = (job.payload or {}).get("action_id")
    if not action_id_raw:
        raise UnrecoverableJobError("payload.action_id is required")
    if job.organization_id is None:
        raise UnrecoverableJobError("provider.reconcile requires organization_id")

    return await reconcile_action(
        db,
        action_id=UUID(str(action_id_raw)),
        organization_id=job.organization_id,
        trigger="reconciliation_job",
    )
