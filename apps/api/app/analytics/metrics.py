"""Derived marketing metrics with safe zero/null denominator handling."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP


_ZERO = Decimal("0")
_HUNDRED = Decimal("100")
_THOUSAND = Decimal("1000")
_SIX = Decimal("0.000001")


def _as_decimal(value) -> Decimal:
    if value is None:
        return _ZERO
    if isinstance(value, Decimal):
        return value
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return _ZERO


def _quantize(value: Decimal | None) -> Decimal | None:
    if value is None:
        return None
    return value.quantize(_SIX, rounding=ROUND_HALF_UP)


@dataclass(frozen=True)
class DerivedMetrics:
    ctr: Decimal | None
    cpc: Decimal | None
    cpm: Decimal | None
    cpl: Decimal | None
    cpa: Decimal | None
    roas: Decimal | None


def compute_derived_metrics(
    *,
    impressions: int | float | Decimal | None,
    clicks: int | float | Decimal | None,
    spend: int | float | Decimal | None,
    conversions: int | float | Decimal | None,
    leads: int | float | Decimal | None,
    revenue: int | float | Decimal | None,
) -> DerivedMetrics:
    """
    Compute CTR/CPC/CPM/CPL/CPA/ROAS.

    Zero or missing denominators yield None — never ZeroDivisionError and never
    invent a rate from empty traffic.
    """
    imp = _as_decimal(impressions)
    clk = _as_decimal(clicks)
    sp = _as_decimal(spend)
    conv = _as_decimal(conversions)
    ld = _as_decimal(leads)
    rev = _as_decimal(revenue)

    ctr = (clk / imp * _HUNDRED) if imp > 0 else None
    cpc = (sp / clk) if clk > 0 else None
    cpm = (sp / imp * _THOUSAND) if imp > 0 else None
    cpl = (sp / ld) if ld > 0 else None
    cpa = (sp / conv) if conv > 0 else None
    roas = (rev / sp) if sp > 0 else None

    return DerivedMetrics(
        ctr=_quantize(ctr),
        cpc=_quantize(cpc),
        cpm=_quantize(cpm),
        cpl=_quantize(cpl),
        cpa=_quantize(cpa),
        roas=_quantize(roas),
    )
