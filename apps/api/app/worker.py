"""
Background worker.

Runs from the same image as the API with a different command:

    python -m app.worker

Deliberately built on the existing PostgreSQL-backed queue rather than adding a
broker. The queue already provides atomic claiming, leases, retries with
backoff and lease recovery (P0-9); introducing Celery or arq would add an
operational dependency without adding a capability we need.

Each cycle:
  1. reclaim jobs whose worker died (expired leases),
  2. claim and execute a batch of due jobs,
  3. sleep, unless there was a full batch — then poll again immediately.

Each cycle uses its own database session so that one poisoned job cannot leave a
dirty session behind for the next.
"""

from __future__ import annotations

import asyncio
import logging
import signal
import sys
from dataclasses import dataclass, field

from app.core.config import get_settings
from app.db.session import AsyncSessionLocal, engine
from app.jobs.queue import default_worker_id
from app.jobs.registry import build_queue
from app.jobs.registry import registered_job_types
from app.services.usage_service import flush_usage, start_usage_buffer

logger = logging.getLogger("growthos.worker")


@dataclass
class WorkerStats:
    cycles: int = 0
    claimed: int = 0
    completed: int = 0
    failed: int = 0
    reclaimed: int = 0
    errors: list[str] = field(default_factory=list)


class Worker:
    def __init__(
        self,
        *,
        worker_id: str | None = None,
        batch_size: int | None = None,
        poll_interval: float | None = None,
        lease_seconds: int | None = None,
    ) -> None:
        settings = get_settings()
        self.worker_id = worker_id or default_worker_id()
        self.batch_size = batch_size or settings.worker_batch_size
        self.poll_interval = (
            poll_interval if poll_interval is not None else settings.worker_poll_interval_seconds
        )
        self.lease_seconds = lease_seconds or settings.worker_lease_seconds
        self.stats = WorkerStats()
        self._stopping = asyncio.Event()

    def request_stop(self) -> None:
        """Finish the current cycle, then exit. Never abandons a claimed job."""
        logger.info("Worker %s received stop signal; finishing current cycle", self.worker_id)
        self._stopping.set()

    async def run_once(self) -> int:
        """One cycle. Returns the number of jobs executed."""
        from app.models.enums import JobStatus

        # Jobs consume AI and media the same way requests do; buffer their usage
        # and write it once the cycle's own transaction is settled.
        start_usage_buffer()

        async with AsyncSessionLocal() as db:
            queue = build_queue(db, worker_id=self.worker_id, lease_seconds=self.lease_seconds)
            try:
                reclaimed = await queue.reap_expired_leases()
                if reclaimed:
                    logger.warning(
                        "Reclaimed %d job(s) from workers that stopped heartbeating", reclaimed
                    )
                    self.stats.reclaimed += reclaimed

                processed = await queue.process_due(limit=self.batch_size)
                await db.commit()
            except Exception as exc:
                await db.rollback()
                # A failure in the cycle itself (not in a handler) must not kill
                # the worker: the next cycle re-reads state from the database.
                logger.exception("Worker cycle failed")
                self.stats.errors.append(str(exc))
                await flush_usage()
                return 0

        await flush_usage()

        from app.observability.metrics import record_job

        self.stats.cycles += 1
        self.stats.claimed += len(processed)
        for job in processed:
            record_job(job_type=job.job_type, status=job.status.value)
            if job.status == JobStatus.completed:
                self.stats.completed += 1
                logger.info("Job completed id=%s type=%s", job.id, job.job_type)
            elif job.status == JobStatus.failed:
                self.stats.failed += 1
                logger.error(
                    "Job failed id=%s type=%s attempts=%s error=%s",
                    job.id,
                    job.job_type,
                    job.attempts,
                    job.error,
                )
            else:
                logger.warning(
                    "Job will retry id=%s type=%s attempts=%s error=%s",
                    job.id,
                    job.job_type,
                    job.attempts,
                    job.error,
                )
        return len(processed)

    async def run_forever(self) -> WorkerStats:
        logger.info(
            "Worker %s started (batch=%d, poll=%.1fs, lease=%ds, types=%s)",
            self.worker_id,
            self.batch_size,
            self.poll_interval,
            self.lease_seconds,
            ", ".join(registered_job_types()),
        )
        while not self._stopping.is_set():
            executed = await self.run_once()
            if executed >= self.batch_size:
                # The queue is backed up; do not sleep through it.
                continue
            try:
                await asyncio.wait_for(self._stopping.wait(), timeout=self.poll_interval)
            except asyncio.TimeoutError:
                pass
        logger.info(
            "Worker %s stopped after %d cycles (completed=%d failed=%d reclaimed=%d)",
            self.worker_id,
            self.stats.cycles,
            self.stats.completed,
            self.stats.failed,
            self.stats.reclaimed,
        )
        return self.stats


async def main() -> int:
    from app.core.startup_checks import ConfigurationError, validate_configuration
    from app.observability.logging import configure_logging

    configure_logging(service="growthos-worker")

    try:
        # The worker touches the same providers, storage and secrets as the API,
        # so it must refuse to start under the same conditions.
        validate_configuration()
    except ConfigurationError as exc:
        logger.critical("%s", exc)
        return 2

    worker = Worker()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, worker.request_stop)
        except NotImplementedError:  # pragma: no cover - Windows
            signal.signal(sig, lambda *_: worker.request_stop())

    try:
        await worker.run_forever()
    finally:
        await engine.dispose()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
