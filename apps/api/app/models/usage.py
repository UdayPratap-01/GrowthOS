"""
Organization-scoped usage records.

One row per metered event, not a running total. Totals are derived, which means
a mistake can be investigated and corrected; a counter that was incremented
wrongly is simply wrong forever with no way to find out why.

Idempotency is a column, not a convention. Jobs retry, webhooks are redelivered
and users double-click, so every writer supplies a key derived from the thing
that happened — a job id, an asset id — and the unique index makes a repeat a
no-op. Billing that double-counts a retried video generation is a refund
request and a support ticket.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import JSON, DateTime, ForeignKey, Index, Integer, Numeric, String
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class UsageRecord(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "usage_records"
    __table_args__ = (
        # The query behind every invoice and every quota check.
        Index("ix_usage_records_org_period_metric", "organization_id", "period", "metric"),
    )

    organization_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    #: Optional attribution for per-client reporting; usage is billed to the org.
    client_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("clients.id", ondelete="SET NULL"), nullable=True
    )

    metric: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    #: Numeric so token counts and byte counts share one column without overflow.
    quantity: Mapped[float] = mapped_column(Numeric(18, 4), nullable=False, default=0)
    #: Billing period this falls in, as YYYY-MM. Stored rather than derived so a
    #: late-arriving record cannot silently land in the wrong month.
    period: Mapped[str] = mapped_column(String(7), nullable=False, index=True)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    #: Unique. A retried job writing the same key changes nothing.
    idempotency_key: Mapped[str] = mapped_column(
        String(255), unique=True, nullable=False, index=True
    )

    #: Provider, model, job id — enough to explain a line on an invoice.
    details: Mapped[dict] = mapped_column(JSON, default=dict)
    #: Kept for quick sanity checks against the derived totals.
    unit_count: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
