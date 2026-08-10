"""
Usage metering.

What this is for: knowing what each organization consumed, so billing can be
built on top and quotas can be enforced. What it deliberately is not: pricing.
No rate, currency or plan cost appears here. Prices change and vary by contract;
consumption is a fact. Mixing them means a price change silently rewrites
history.

Recording is idempotent. Every caller supplies a key derived from the event —
`image:{asset_id}`, `job:{job_id}` — so a retried job, a redelivered webhook or
an impatient double-click records once.
"""

from __future__ import annotations

import contextvars
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.usage import UsageRecord

logger = logging.getLogger("growthos.usage")


class Metric:
    """Metered quantities. Values are stable: they end up in stored records."""

    AI_REQUEST = "ai_request"
    AI_TOKENS = "ai_tokens"
    IMAGE_GENERATION = "image_generation"
    VIDEO_GENERATION = "video_generation"
    REPORT_GENERATION = "report_generation"
    STORAGE_BYTES = "storage_bytes"
    INTEGRATION_SYNC = "integration_sync"
    LEAD = "lead"
    CLIENT = "client"

    ALL = (
        AI_REQUEST,
        AI_TOKENS,
        IMAGE_GENERATION,
        VIDEO_GENERATION,
        REPORT_GENERATION,
        STORAGE_BYTES,
        INTEGRATION_SYNC,
        LEAD,
        CLIENT,
    )


#: Counted once and not reset each month — a plan caps how many you may *have*.
STOCK_METRICS = frozenset({Metric.CLIENT, Metric.STORAGE_BYTES})


def current_period(at: datetime | None = None) -> str:
    moment = at or datetime.now(timezone.utc)
    return f"{moment.year:04d}-{moment.month:02d}"


@dataclass
class UsageSummary:
    organization_id: UUID
    period: str
    totals: dict[str, float]

    def get(self, metric: str) -> float:
        return float(self.totals.get(metric, 0.0))


class UsageService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def record(
        self,
        *,
        organization_id: UUID,
        metric: str,
        quantity: float = 1,
        idempotency_key: str,
        client_id: UUID | None = None,
        details: dict | None = None,
        occurred_at: datetime | None = None,
    ) -> UsageRecord | None:
        """
        Record consumption. Returns None when the key was already recorded.

        Never raises on a duplicate: the caller is a job handler or a request
        path whose real work already succeeded, and failing it because the meter
        was written twice would turn an accounting detail into a user-visible
        error.
        """
        if metric not in Metric.ALL:
            raise ValueError(f"Unknown usage metric {metric!r}")

        moment = occurred_at or datetime.now(timezone.utc)
        record = UsageRecord(
            organization_id=organization_id,
            client_id=client_id,
            metric=metric,
            quantity=Decimal(str(quantity)),
            period=current_period(moment),
            occurred_at=moment,
            idempotency_key=idempotency_key,
            details=details or {},
            unit_count=1,
        )
        # The insert runs inside a savepoint, and the row is added inside it too:
        # an object added to the outer transaction would be flushed again later
        # and fail the caller's commit with the duplicate we just handled.
        try:
            async with self.db.begin_nested():
                self.db.add(record)
                await self.db.flush()
        except IntegrityError:
            # Another attempt at the same event got there first. The savepoint
            # rollback usually detaches the row already; expunge covers the case
            # where it does not, so the caller's later commit cannot retry it.
            if record in self.db:
                self.db.expunge(record)
            logger.debug(
                "Duplicate usage record ignored",
                extra={"event": "usage.duplicate", "metric": metric},
            )
            return None

        logger.info(
            "Usage recorded",
            extra={
                "event": "usage.recorded",
                "metric": metric,
                "quantity": float(quantity),
                "org": str(organization_id),
            },
        )
        return record

    async def summary(
        self, organization_id: UUID, *, period: str | None = None
    ) -> UsageSummary:
        target = period or current_period()
        rows = await self.db.execute(
            select(UsageRecord.metric, func.sum(UsageRecord.quantity))
            .where(
                UsageRecord.organization_id == organization_id,
                UsageRecord.period == target,
            )
            .group_by(UsageRecord.metric)
        )
        totals = {metric: float(total or 0) for metric, total in rows.all()}

        # Stock metrics are a standing total, not this month's additions.
        lifetime = await self.db.execute(
            select(UsageRecord.metric, func.sum(UsageRecord.quantity))
            .where(
                UsageRecord.organization_id == organization_id,
                UsageRecord.metric.in_(tuple(STOCK_METRICS)),
            )
            .group_by(UsageRecord.metric)
        )
        for metric, total in lifetime.all():
            totals[metric] = float(total or 0)

        return UsageSummary(organization_id=organization_id, period=target, totals=totals)

    async def total(
        self, organization_id: UUID, metric: str, *, period: str | None = None
    ) -> float:
        query = select(func.sum(UsageRecord.quantity)).where(
            UsageRecord.organization_id == organization_id, UsageRecord.metric == metric
        )
        if metric not in STOCK_METRICS:
            query = query.where(UsageRecord.period == (period or current_period()))
        return float(await self.db.scalar(query) or 0)

    async def timeline(
        self, organization_id: UUID, metric: str, *, limit: int = 100
    ) -> list[UsageRecord]:
        rows = await self.db.scalars(
            select(UsageRecord)
            .where(
                UsageRecord.organization_id == organization_id, UsageRecord.metric == metric
            )
            .order_by(UsageRecord.occurred_at.desc())
            .limit(limit)
        )
        return list(rows)


@dataclass
class PendingUsage:
    organization_id: UUID
    metric: str
    quantity: float
    idempotency_key: str
    client_id: UUID | None = None
    details: dict | None = None


#: Usage accumulated during the current request or job, flushed once at the end.
_pending: contextvars.ContextVar[list[PendingUsage] | None] = contextvars.ContextVar(
    "pending_usage", default=None
)


def start_usage_buffer() -> None:
    _pending.set([])


def queue_usage(entry: PendingUsage) -> bool:
    """
    Defer a usage record to the end of the request.

    Writing immediately would mean opening a second connection while the
    request's own transaction is still open — extra pool pressure in production
    and lock contention on SQLite. Returns False when no buffer is active, so
    the caller can fall back to writing directly.
    """
    buffer = _pending.get()
    if buffer is None:
        return False
    buffer.append(entry)
    return True


async def flush_usage() -> int:
    """
    Write everything buffered during this request or job.

    Uses its own session and commits separately: consumption happened whether or
    not the request's transaction survived. Failures are logged, never raised —
    the user's work is already done and a meter write must not undo it.
    """
    buffer = _pending.get()
    _pending.set(None)
    if not buffer:
        return 0

    from app.db.session import AsyncSessionLocal

    written = 0
    try:
        async with AsyncSessionLocal() as db:
            service = UsageService(db)
            for entry in buffer:
                record = await service.record(
                    organization_id=entry.organization_id,
                    metric=entry.metric,
                    quantity=entry.quantity,
                    idempotency_key=entry.idempotency_key,
                    client_id=entry.client_id,
                    details=entry.details,
                )
                written += 1 if record is not None else 0
            await db.commit()
    except Exception:
        logger.exception("Failed to flush usage", extra={"event": "usage.flush_error"})
    return written


async def meter(
    db: AsyncSession,
    *,
    organization_id: UUID | None,
    metric: str,
    idempotency_key: str,
    quantity: float = 1,
    client_id: UUID | None = None,
    details: dict | None = None,
) -> None:
    """
    Fire-and-forget helper for call sites whose real work already succeeded.

    Metering must never be the reason a generation or a sync fails, so errors
    are logged and swallowed here. Losing a meter reading is a billing problem
    to reconcile; failing the user's request because of one is worse.
    """
    if organization_id is None:
        return
    try:
        await UsageService(db).record(
            organization_id=organization_id,
            metric=metric,
            quantity=quantity,
            idempotency_key=idempotency_key,
            client_id=client_id,
            details=details,
        )
    except Exception:
        logger.exception("Failed to record usage", extra={"event": "usage.error", "metric": metric})
