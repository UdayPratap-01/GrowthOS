"""Search Console date range resolution with API latency awareness."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta


GSC_DATA_LATENCY_DAYS = 3


@dataclass(frozen=True)
class DateRange:
    start: date
    end: date
    requested_end: date
    note: str = ""

    @property
    def days(self) -> int:
        return (self.end - self.start).days + 1


def resolve_preset_range(preset: str, *, today: date | None = None) -> DateRange:
    today = today or date.today()
    effective_end = today - timedelta(days=GSC_DATA_LATENCY_DAYS)
    mapping = {"last_7_days": 7, "last_28_days": 28, "last_90_days": 90}
    if preset not in mapping:
        raise ValueError(f"Unsupported preset: {preset}")
    days = mapping[preset]
    start = effective_end - timedelta(days=days - 1)
    return DateRange(
        start=start,
        end=effective_end,
        requested_end=today,
        note=f"Effective end adjusted by {GSC_DATA_LATENCY_DAYS} day(s) for Search Console data latency.",
    )


def resolve_custom_range(start: date, end: date, *, today: date | None = None) -> DateRange:
    today = today or date.today()
    if start > end:
        raise ValueError("start_date must be on or before end_date")
    span = (end - start).days + 1
    if span > 90:
        raise ValueError("Custom range cannot exceed 90 days")
    effective_end = min(end, today - timedelta(days=GSC_DATA_LATENCY_DAYS))
    if effective_end < start:
        raise ValueError("Date range is too recent for complete Search Console data")
    return DateRange(
        start=start,
        end=effective_end,
        requested_end=end,
        note=f"Effective end may be earlier than requested due to Search Console latency ({GSC_DATA_LATENCY_DAYS} days).",
    )


def previous_equivalent_range(current: DateRange) -> DateRange:
    days = current.days
    prev_end = current.start - timedelta(days=1)
    prev_start = prev_end - timedelta(days=days - 1)
    return DateRange(start=prev_start, end=prev_end, requested_end=prev_end, note="Previous equivalent period for comparison.")
