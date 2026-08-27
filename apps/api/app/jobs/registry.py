"""
The single place where job types are mapped to handlers.

Both the worker process and the inline development path build their queue from
`build_queue()`, so a handler registered here is reachable from either. A job
type that is enqueued but not registered fails with a clear reason rather than
sitting in the queue forever (see `JobQueue._run_claimed`).
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.jobs.queue import JobQueue

# Job type constants — referenced by producers so a typo is an import error
# rather than a job that never runs.
IMAGE_GENERATE = "media.generate_image"
VIDEO_GENERATE = "media.generate_video"
VIDEO_POLL = "media.poll_video"
REPORT_GENERATE = "report.generate"
ANALYTICS_SYNC = "analytics.sync"
LEAD_BACKFILL = "leads.backfill_meta_contact"
PUBLISH_DUE = "publish_due"
CAMPAIGN_GENERATE = "campaign.generate"
CAMPAIGN_RECONCILE = "campaign.reconcile"
AUTOPILOT_SCHEDULER_TICK = "autopilot.scheduler_tick"
AUTOPILOT_CYCLE = "autopilot.cycle"
PROVIDER_RECONCILE = "provider.reconcile"
ANALYTICS_INGEST = "analytics.ingest"
ANALYTICS_ANALYZE = "analytics.analyze"


def build_queue(db: AsyncSession, **kwargs) -> JobQueue:
    from app.jobs import handlers

    queue = JobQueue(db, **kwargs)
    queue.register(IMAGE_GENERATE, handlers.handle_generate_image)
    queue.register(VIDEO_GENERATE, handlers.handle_generate_video)
    queue.register(VIDEO_POLL, handlers.handle_poll_video)
    queue.register(REPORT_GENERATE, handlers.handle_generate_report)
    queue.register(ANALYTICS_SYNC, handlers.handle_analytics_sync)
    queue.register(ANALYTICS_INGEST, handlers.handle_analytics_ingest)
    queue.register(ANALYTICS_ANALYZE, handlers.handle_analytics_analyze)
    queue.register(LEAD_BACKFILL, handlers.handle_lead_backfill)
    queue.register(PUBLISH_DUE, handlers.handle_publish_due)
    queue.register(CAMPAIGN_GENERATE, handlers.handle_generate_campaign)
    queue.register(CAMPAIGN_RECONCILE, handlers.handle_reconcile_campaign_run)
    queue.register(AUTOPILOT_SCHEDULER_TICK, handlers.handle_autopilot_scheduler_tick)
    queue.register(AUTOPILOT_CYCLE, handlers.handle_autopilot_cycle)
    queue.register(PROVIDER_RECONCILE, handlers.handle_provider_reconcile)
    return queue


def registered_job_types() -> tuple[str, ...]:
    return (
        IMAGE_GENERATE,
        VIDEO_GENERATE,
        VIDEO_POLL,
        REPORT_GENERATE,
        ANALYTICS_SYNC,
        ANALYTICS_INGEST,
        ANALYTICS_ANALYZE,
        LEAD_BACKFILL,
        PUBLISH_DUE,
        CAMPAIGN_GENERATE,
        CAMPAIGN_RECONCILE,
        AUTOPILOT_SCHEDULER_TICK,
        AUTOPILOT_CYCLE,
        PROVIDER_RECONCILE,
    )
