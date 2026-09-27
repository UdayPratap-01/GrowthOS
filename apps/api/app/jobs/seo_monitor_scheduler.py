"""
Scheduled SEO monitoring — enqueues bounded monitor cycles via the existing job queue.

The scheduler never crawls or syncs directly. Each tick discovers monitoring-enabled
organizations and enqueues one `seo.monitor_cycle` job per eligible tenant.
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
from app.models.automation import BackgroundJob
from app.models.enums import JobStatus
from app.models.organization import Organization
from app.models.seo_monitoring import SeoMonitoringConfig

logger = logging.getLogger(__name__)

SCHEDULER_ACTOR_USER_ID = UUID("00000000-0000-0000-0000-000000000002")

SEO_MONITOR_SCHEDULER_TICK = "seo.monitor_scheduler_tick"
SEO_MONITOR_CYCLE = "seo.monitor_cycle"

MIN_INTERVAL_MINUTES = 60
MAX_INTERVAL_MINUTES = 7 * 24 * 60
MAX_ORGS_PER_CYCLE = 200


@dataclass(frozen=True)
class EnqueueMonitorResult:
    job: BackgroundJob | None
    skipped: bool
    reason: str | None = None


def validate_seo_monitor_scheduler_settings(settings: Settings) -> list[str]:
    errors: list[str] = []
    interval = settings.seo_monitor_interval_minutes
    max_orgs = settings.seo_monitor_max_orgs_per_cycle
    if interval <= 0:
        errors.append("SEO_MONITOR_INTERVAL_MINUTES must be positive.")
    elif interval < MIN_INTERVAL_MINUTES:
        errors.append(
            f"SEO_MONITOR_INTERVAL_MINUTES must be at least {MIN_INTERVAL_MINUTES}."
        )
    elif interval > MAX_INTERVAL_MINUTES:
        errors.append(f"SEO_MONITOR_INTERVAL_MINUTES must not exceed {MAX_INTERVAL_MINUTES}.")
    if max_orgs <= 0:
        errors.append("SEO_MONITOR_MAX_ORGS_PER_CYCLE must be positive.")
    elif max_orgs > MAX_ORGS_PER_CYCLE:
        errors.append(f"SEO_MONITOR_MAX_ORGS_PER_CYCLE must not exceed {MAX_ORGS_PER_CYCLE}.")
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
    return f"seo-monitor-scheduler:{window_start.isoformat()}"


def monitor_cycle_dedupe_key(organization_id: UUID, window_start: datetime) -> str:
    return f"seo-monitor:{organization_id}:{window_start.isoformat()}"


async def discover_monitor_targets(
    db: AsyncSession,
    *,
    max_orgs: int,
) -> list[tuple[Organization, SeoMonitoringConfig]]:
    configs = (
        await db.execute(
            select(SeoMonitoringConfig)
            .where(SeoMonitoringConfig.monitoring_enabled.is_(True))
            .order_by(SeoMonitoringConfig.updated_at.asc())
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

    targets: list[tuple[Organization, SeoMonitoringConfig]] = []
    for config in configs:
        org = org_by_id.get(config.organization_id)
        if org is not None:
            targets.append((org, config))
    return targets


async def organization_has_inflight_monitor_cycle(
    db: AsyncSession,
    organization_id: UUID,
) -> bool:
    existing = await db.scalar(
        select(BackgroundJob.id)
        .where(
            BackgroundJob.organization_id == organization_id,
            BackgroundJob.job_type == SEO_MONITOR_CYCLE,
            BackgroundJob.status.in_((JobStatus.queued, JobStatus.retrying, JobStatus.running)),
        )
        .limit(1)
    )
    return existing is not None


async def enqueue_monitor_cycle(
    db: AsyncSession,
    *,
    organization: Organization,
    window_start: datetime,
    trigger: str = "scheduler",
) -> EnqueueMonitorResult:
    config = await db.scalar(
        select(SeoMonitoringConfig)
        .where(SeoMonitoringConfig.organization_id == organization.id)
        .limit(1)
    )
    if config is None or not config.monitoring_enabled:
        return EnqueueMonitorResult(job=None, skipped=True, reason="MONITORING_DISABLED")

    dedupe_key = monitor_cycle_dedupe_key(organization.id, window_start)
    existing = await db.scalar(
        select(BackgroundJob).where(BackgroundJob.dedupe_key == dedupe_key).limit(1)
    )
    if existing is not None:
        return EnqueueMonitorResult(job=existing, skipped=False)

    if await organization_has_inflight_monitor_cycle(db, organization.id):
        logger.info(
            "seo monitor cycle skipped overlapping org=%s window=%s",
            organization.id,
            window_start.isoformat(),
        )
        return EnqueueMonitorResult(job=None, skipped=True, reason="OVERLAPPING_CYCLE")

    job = await JobQueue(db).enqueue(
        job_type=SEO_MONITOR_CYCLE,
        payload={
            "window": window_start.isoformat(),
            "trigger": trigger,
        },
        organization_id=organization.id,
        dedupe_key=dedupe_key,
        max_attempts=3,
    )
    logger.info(
        "seo monitor cycle enqueued org=%s job_id=%s window=%s trigger=%s",
        organization.id,
        job.id,
        window_start.isoformat(),
        trigger,
    )
    return EnqueueMonitorResult(job=job, skipped=False)


async def schedule_next_monitor_tick(db: AsyncSession) -> BackgroundJob | None:
    settings = get_settings()
    if not settings.seo_monitor_scheduler_enabled:
        return None

    now = datetime.now(timezone.utc)
    interval = settings.seo_monitor_interval_minutes
    next_run = now + timedelta(minutes=interval)
    next_window = scheduled_window_start(next_run, interval)
    return await JobQueue(db).enqueue(
        job_type=SEO_MONITOR_SCHEDULER_TICK,
        payload={"window": next_window.isoformat()},
        organization_id=None,
        run_after=next_run,
        dedupe_key=scheduler_tick_dedupe_key(next_window),
        max_attempts=3,
    )


async def ensure_seo_monitor_tick(db: AsyncSession) -> BackgroundJob | None:
    settings = get_settings()
    if not settings.seo_monitor_scheduler_enabled:
        return None

    existing = await db.scalar(
        select(BackgroundJob)
        .where(
            BackgroundJob.job_type == SEO_MONITOR_SCHEDULER_TICK,
            BackgroundJob.status.in_((JobStatus.queued, JobStatus.retrying, JobStatus.running)),
        )
        .limit(1)
    )
    if existing is not None:
        return existing

    now = datetime.now(timezone.utc)
    window = scheduled_window_start(now, settings.seo_monitor_interval_minutes)
    job = await JobQueue(db).enqueue(
        job_type=SEO_MONITOR_SCHEDULER_TICK,
        payload={"window": window.isoformat()},
        organization_id=None,
        run_after=now,
        dedupe_key=scheduler_tick_dedupe_key(window),
        max_attempts=3,
    )
    logger.info("seo monitor scheduler tick ensured job_id=%s window=%s", job.id, window.isoformat())
    return job
