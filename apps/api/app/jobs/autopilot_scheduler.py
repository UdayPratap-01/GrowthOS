"""
Scheduled autopilot execution — enqueues bounded cycles via the existing job queue.

The scheduler never calls platform APIs directly. Each tick discovers tenant targets,
enqueues one `autopilot.cycle` job per eligible client, and the worker executes those
jobs through `AutopilotOrchestratorService` (same path as POST /autopilot/cycle).
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
from app.models.automation import AutonomySettings, BackgroundJob
from app.models.client import Client
from app.models.enums import ClientStatus, JobStatus
from app.models.organization import Organization
from app.services.autonomy_service import AutonomyService

logger = logging.getLogger(__name__)

# Stable actor id for scheduled cycles — not a real user row; audit records use trigger=scheduler.
SCHEDULER_ACTOR_USER_ID = UUID("00000000-0000-0000-0000-000000000001")

AUTOPILOT_SCHEDULER_TICK = "autopilot.scheduler_tick"
AUTOPILOT_CYCLE = "autopilot.cycle"

MIN_INTERVAL_MINUTES = 5
MAX_INTERVAL_MINUTES = 24 * 60
MAX_ORGS_PER_CYCLE = 500


@dataclass(frozen=True)
class EnqueueCycleResult:
    job: BackgroundJob | None
    skipped: bool
    reason: str | None = None


def validate_autopilot_scheduler_settings(settings: Settings) -> list[str]:
    """Return configuration errors for scheduler settings (empty when valid)."""
    errors: list[str] = []
    interval = settings.autopilot_interval_minutes
    max_orgs = settings.autopilot_max_orgs_per_cycle
    if interval <= 0:
        errors.append("AUTOPILOT_INTERVAL_MINUTES must be positive.")
    elif interval < MIN_INTERVAL_MINUTES:
        errors.append(
            f"AUTOPILOT_INTERVAL_MINUTES must be at least {MIN_INTERVAL_MINUTES} "
            "(shorter intervals risk runaway action generation)."
        )
    elif interval > MAX_INTERVAL_MINUTES:
        errors.append(f"AUTOPILOT_INTERVAL_MINUTES must not exceed {MAX_INTERVAL_MINUTES}.")
    if max_orgs <= 0:
        errors.append("AUTOPILOT_MAX_ORGS_PER_CYCLE must be positive.")
    elif max_orgs > MAX_ORGS_PER_CYCLE:
        errors.append(f"AUTOPILOT_MAX_ORGS_PER_CYCLE must not exceed {MAX_ORGS_PER_CYCLE}.")
    return errors


def scheduled_window_start(now: datetime, interval_minutes: int) -> datetime:
    """Floor `now` to the start of the current scheduler window."""
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    epoch = datetime(1970, 1, 1, tzinfo=timezone.utc)
    window_seconds = interval_minutes * 60
    elapsed = int((now - epoch).total_seconds())
    window_index = elapsed // window_seconds
    return epoch + timedelta(seconds=window_index * window_seconds)


def scheduler_tick_dedupe_key(window_start: datetime) -> str:
    return f"autopilot-scheduler:{window_start.isoformat()}"


def autopilot_cycle_dedupe_key(organization_id: UUID, client_id: UUID, window_start: datetime) -> str:
    return f"autopilot:{organization_id}:{client_id}:{window_start.isoformat()}"


async def discover_autopilot_targets(
    db: AsyncSession,
    *,
    max_orgs: int,
) -> list[tuple[Organization, Client]]:
    """
    Return (organization, client) pairs eligible for a scheduled cycle.

    An org is included when effective `automation_enabled` is true for at least one
    active client. At most `max_orgs` distinct organizations are returned.
    """
    clients = (
        await db.execute(
            select(Client)
            .where(
                Client.status == ClientStatus.active,
                Client.archived_at.is_(None),
            )
            .order_by(Client.organization_id.asc(), Client.created_at.asc())
        )
    ).scalars().all()
    if not clients:
        return []

    org_ids = {c.organization_id for c in clients}
    org_rows = (
        await db.execute(select(Organization).where(Organization.id.in_(org_ids)))
    ).scalars().all()
    org_by_id = {org.id: org for org in org_rows}

    autonomy = AutonomyService(db)
    targets: list[tuple[Organization, Client]] = []
    included_orgs: set[UUID] = set()

    for client in clients:
        org = org_by_id.get(client.organization_id)
        if org is None:
            continue
        if client.organization_id not in included_orgs:
            if len(included_orgs) >= max_orgs:
                continue
            settings = await autonomy.get_effective(org.id, client.id)
            if not settings.automation_enabled:
                continue
            included_orgs.add(org.id)
            targets.append((org, client))
        else:
            settings = await autonomy.get_effective(org.id, client.id)
            if settings.automation_enabled:
                targets.append((org, client))

    return targets


async def organization_has_inflight_autopilot_cycle(
    db: AsyncSession,
    organization_id: UUID,
) -> bool:
    """True when this org already has a queued, retrying, or running autopilot cycle job."""
    existing = await db.scalar(
        select(BackgroundJob.id)
        .where(
            BackgroundJob.organization_id == organization_id,
            BackgroundJob.job_type == AUTOPILOT_CYCLE,
            BackgroundJob.status.in_((JobStatus.queued, JobStatus.retrying, JobStatus.running)),
        )
        .limit(1)
    )
    return existing is not None


async def enqueue_autopilot_cycle(
    db: AsyncSession,
    *,
    organization: Organization,
    client: Client,
    window_start: datetime,
) -> EnqueueCycleResult:
    """Enqueue one bounded autopilot cycle for a tenant client (idempotent per window)."""
    if client.organization_id != organization.id:
        return EnqueueCycleResult(job=None, skipped=True, reason="TENANT_MISMATCH")

    settings = await AutonomyService(db).get_effective(organization.id, client.id)
    if not settings.automation_enabled:
        return EnqueueCycleResult(job=None, skipped=True, reason="AUTOMATION_DISABLED")

    dedupe_key = autopilot_cycle_dedupe_key(organization.id, client.id, window_start)
    existing = await db.scalar(
        select(BackgroundJob).where(BackgroundJob.dedupe_key == dedupe_key).limit(1)
    )
    if existing is not None:
        return EnqueueCycleResult(job=existing, skipped=False)

    if await organization_has_inflight_autopilot_cycle(db, organization.id):
        logger.info(
            "autopilot cycle skipped overlapping org=%s client=%s window=%s",
            organization.id,
            client.id,
            window_start.isoformat(),
        )
        return EnqueueCycleResult(job=None, skipped=True, reason="OVERLAPPING_CYCLE")

    job = await JobQueue(db).enqueue(
        job_type=AUTOPILOT_CYCLE,
        payload={
            "client_id": str(client.id),
            "window": window_start.isoformat(),
            "trigger": "scheduler",
        },
        organization_id=organization.id,
        dedupe_key=dedupe_key,
        max_attempts=3,
    )
    logger.info(
        "autopilot cycle enqueued org=%s client=%s job_id=%s window=%s demo=%s",
        organization.id,
        client.id,
        job.id,
        window_start.isoformat(),
        organization.demo_mode,
    )
    return EnqueueCycleResult(job=job, skipped=False)


async def schedule_next_scheduler_tick(db: AsyncSession) -> BackgroundJob | None:
    """Enqueue the next scheduler tick when the scheduler is enabled."""
    settings = get_settings()
    if not settings.autopilot_scheduler_enabled:
        return None

    now = datetime.now(timezone.utc)
    interval = settings.autopilot_interval_minutes
    next_run = now + timedelta(minutes=interval)
    next_window = scheduled_window_start(next_run, interval)
    return await JobQueue(db).enqueue(
        job_type=AUTOPILOT_SCHEDULER_TICK,
        payload={"window": next_window.isoformat()},
        organization_id=None,
        run_after=next_run,
        dedupe_key=scheduler_tick_dedupe_key(next_window),
        max_attempts=3,
    )


async def ensure_scheduler_tick(db: AsyncSession) -> BackgroundJob | None:
    """
    Ensure a scheduler tick is waiting when enabled.

    Safe to call from every worker cycle: returns an existing in-flight tick or
    enqueues one idempotently for the current window.
    """
    settings = get_settings()
    if not settings.autopilot_scheduler_enabled:
        return None

    existing = await db.scalar(
        select(BackgroundJob)
        .where(
            BackgroundJob.job_type == AUTOPILOT_SCHEDULER_TICK,
            BackgroundJob.status.in_((JobStatus.queued, JobStatus.retrying, JobStatus.running)),
        )
        .limit(1)
    )
    if existing is not None:
        return existing

    now = datetime.now(timezone.utc)
    window = scheduled_window_start(now, settings.autopilot_interval_minutes)
    job = await JobQueue(db).enqueue(
        job_type=AUTOPILOT_SCHEDULER_TICK,
        payload={"window": window.isoformat()},
        organization_id=None,
        run_after=now,
        dedupe_key=scheduler_tick_dedupe_key(window),
        max_attempts=3,
    )
    logger.info(
        "autopilot scheduler tick ensured job_id=%s window=%s",
        job.id,
        window.isoformat(),
    )
    return job


async def count_orgs_with_automation_enabled(db: AsyncSession) -> int:
    """Count distinct organizations with at least one automation-enabled client."""
    rows = await db.execute(
        select(AutonomySettings.organization_id)
        .where(AutonomySettings.automation_enabled.is_(True))
        .distinct()
    )
    return len(rows.scalars().all())
