"""Safe period-over-period comparison calculations."""

from __future__ import annotations

from typing import Any


def pct_change(current: float | None, previous: float | None) -> dict[str, Any]:
    if current is None or previous is None:
        return {"change_pct": None, "change_label": "no_previous_data"}
    if previous == 0:
        if current == 0:
            return {"change_pct": 0.0, "change_label": "unchanged_zero_baseline"}
        return {"change_pct": None, "change_label": "increase_from_zero_baseline", "absolute_change": current}
    change = ((current - previous) / previous) * 100.0
    label = "increase" if change > 0 else "decrease" if change < 0 else "unchanged"
    return {"change_pct": round(change, 2), "change_label": label, "absolute_change": current - previous}


def position_change(current: float | None, previous: float | None) -> dict[str, Any]:
    if current is None or previous is None:
        return {"position_change": None, "change_label": "no_previous_data"}
    delta = round(previous - current, 2)
    if delta > 0:
        label = "improved"
    elif delta < 0:
        label = "declined"
    else:
        label = "unchanged"
    return {"position_change": delta, "change_label": label}
