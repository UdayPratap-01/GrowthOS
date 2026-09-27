"""
Scheduled SEO weekly report generation — enqueues bounded report jobs via the existing job queue.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.jobs.queue import JobQueue
from app.jobs.registry import SEO_REPORT_GENERATE
from app.models.automation import BackgroundJob
from app.models.enums import JobStatus
from app.models.organization import Organization
from app.models.seo_weekly_report import SeoReportConfig
from app.services.seo_weekly_report_service import weekly_period_for_date

logger = logging.getLogger(__name__)

SEO_REPORT_SCHEDULER_TICK = "seo.report_scheduler_tick"

MIN_INTERVAL_MINUTES = 24 * 60
MAX_INTERVAL_MINUTES = 7 * 24 * 60
MAX_ORGS_PER_CYCLE = 100


@dataclass(frozen=True)
class EnqueueReportResult:
    job: BackgroundJob | None
    skipped: bool
    reason: str | None = None


def validate_seo_report_scheduler_settings(settings: Settings) -> list[str]:
    errors: list[str] = []
    interval = settings.seo_weekly_report_interval_minutes
    max_orgs = settings.seo_weekly_report_max_orgs_per_cycle
    if interval <= 0:
        errors.append("SEO_WEEKLY_REPORT_INTERVAL_MINUTES must be positive.")
    elif interval < MIN_INTERVAL_MINUTES:
        errors.append(f"SEO_WEEKLY_REPORT_INTERVAL_MINUTES must be at least {MIN_INTERVAL_MINUTES}.")
    elif interval > MAX_INTERVAL_MINUTES:
        errors.append(f"SEO_WEEKLY_REPORT_INTERVAL_MINUTES must not exceed {MAX_INTERVAL_MINUTES}.")
    if max_orgs <= 0:
        errors.append("SEO_WEEKLY_REPORT_MAX_ORGS_PER_CYCLE must be positive.")
    elif max_orgs > MAX_ORGS_PER_CYCLE:
        errors.append(f"SEO_WEEKLY_REPORT_MAX_ORGS_PER_CYCLE must not exceed {MAX_ORGS_PER_CYCLE}.")
    return errors


def scheduled_window_start(now: datetime, interval_minutes: int) -> datetime:
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    epoch = datetime(1970, 1, 1, tzinfo=timezone.utc)
    window_seconds = interval_minutes * 60
    elapsed = int((now - epoch).total_seconds())
    window_index = elapsed // window_seconds
    return epoch + timedelta(seconds=window_index * window_seconds)


def scheduler_tick_dedupe_key(window_start: datetime) -> str:
    return f"seo-report-scheduler:{window_start.isoformat()}"


async def discover_report_targets(
    db: AsyncSession,
    *,
    max_orgs: int,
) -> list[tuple[Organization, SeoReportConfig]]:
    configs = (
        await db.execute(
            select(SeoReportConfig)
            .where(SeoReportConfig.reporting_enabled.is_(True))
            .order_by(SeoReportConfig.updated_at.asc())
            .limit(max_orgs)
        )
    ).scalars().all()
    if not configs:
        return []

    org_ids = {c.organization_id for c in configs}
    org_rows = (
        await db.execute(select(Organization).where(Organization.id.in_(org_ids)))
    ).scalars().all()
    org_by_id = {org.id: org for org in org_rows}
    return [(org_by_id[c.organization_id], c) for c in configs if c.organization_id in org_by_id]


async def organization_has_inflight_report_job(
    db: AsyncSession,
    organization_id: UUID,
) -> bool:
    existing = await db.scalar(
        select(BackgroundJob.id)
        .where(
            BackgroundJob.organization_id == organization_id,
            BackgroundJob.job_type == SEO_REPORT_GENERATE,
            BackgroundJob.status.in_((JobStatus.queued, JobStatus.retrying, JobStatus.running)),
        )
        .limit(1)
    )
    return existing is not None


async def enqueue_weekly_report_for_org(
    db: AsyncSession,
    *,
    organization: Organization,
    window_start: datetime,
) -> EnqueueReportResult:
    from app.services.seo_weekly_report_service import SeoWeeklyReportService

    config = await db.scalar(
        select(SeoReportConfig).where(SeoReportConfig.organization_id == organization.id).limit(1)
    )
    if config is None or not config.reporting_enabled:
        return EnqueueReportResult(job=None, skipped=True, reason="REPORTING_DISABLED")

    if await organization_has_inflight_report_job(db, organization.id):
        return EnqueueReportResult(job=None, skipped=True, reason="OVERLAPPING_JOB")

    period_start, period_end = weekly_period_for_date(anchor=window_start.date())
    svc = SeoWeeklyReportService(db)
    existing = await svc.get_existing_report(
        organization_id=organization.id,
        period_start=period_start,
        period_end=period_end,
    )
    if existing:
        return EnqueueReportResult(job=None, skipped=True, reason="ALREADY_COMPLETED")

    report = await svc.enqueue_generate(
        organization_id=organization.id,
        period_start=period_start,
        period_end=period_end,
    )
    job = await db.get(BackgroundJob, report.background_job_id) if report.background_job_id else None
    logger.info(
        "seo weekly report enqueued org=%s report_id=%s period=%s..%s",
        organization.id,
        report.id,
        period_start,
        period_end,
    )
    return EnqueueReportResult(job=job, skipped=False)


async def schedule_next_report_tick(db: AsyncSession) -> BackgroundJob | None:
    settings = get_settings()
    if not settings.seo_weekly_report_scheduler_enabled:
        return None
    now = datetime.now(timezone.utc)
    interval = settings.seo_weekly_report_interval_minutes
    next_run = now + timedelta(minutes=interval)
    next_window = scheduled_window_start(next_run, interval)
    return await JobQueue(db).enqueue(
        job_type=SEO_REPORT_SCHEDULER_TICK,
        payload={"window": next_window.isoformat()},
        organization_id=None,
        run_after=next_run,
        dedupe_key=scheduler_tick_dedupe_key(next_window),
        max_attempts=3,
    )


async def ensure_seo_report_tick(db: AsyncSession) -> BackgroundJob | None:
    settings = get_settings()
    if not settings.seo_weekly_report_scheduler_enabled:
        return None

    existing = await db.scalar(
        select(BackgroundJob)
        .where(
            BackgroundJob.job_type == SEO_REPORT_SCHEDULER_TICK,
            BackgroundJob.status.in_((JobStatus.queued, JobStatus.retrying, JobStatus.running)),
        )
        .limit(1)
    )
    if existing is not None:
        return existing

    now = datetime.now(timezone.utc)
    window = scheduled_window_start(now, settings.seo_weekly_report_interval_minutes)
    job = await JobQueue(db).enqueue(
        job_type=SEO_REPORT_SCHEDULER_TICK,
        payload={"window": window.isoformat()},
        organization_id=None,
        run_after=now,
        dedupe_key=scheduler_tick_dedupe_key(window),
        max_attempts=3,
    )
    logger.info("seo report scheduler tick ensured job_id=%s window=%s", job.id, window.isoformat())
    return job
