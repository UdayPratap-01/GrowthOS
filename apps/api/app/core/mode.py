"""Demo vs live mode helpers — never silently relabel seed/demo rows as live."""

from __future__ import annotations

import enum

from app.core.config import get_settings
from app.models.organization import Organization


class ExecutionMode(str, enum.Enum):
    """
    How a result was produced. Callers must never collapse these into a bare
    boolean "success" — a simulated execution is not a real one.

    DEMO_DATA       Seeded or illustrative data. Nothing was executed.
    DEMO_EXECUTION  A real code path ran but was simulated; no external side effect
                    and no external ID exists.
    REAL_EXECUTION  An external provider confirmed the operation.
    """

    demo_data = "DEMO_DATA"
    demo_execution = "DEMO_EXECUTION"
    real_execution = "REAL_EXECUTION"


def execution_mode_for(*, demo: bool, external_id: str | None = None) -> ExecutionMode:
    """Classify a completed operation honestly."""
    if demo:
        return ExecutionMode.demo_execution
    if external_id:
        return ExecutionMode.real_execution
    return ExecutionMode.demo_data


def effective_demo_mode(organization: Organization | None = None, *, org_demo: bool | None = None) -> bool:
    """True when either org or env says demo. Live mode requires both false."""
    settings = get_settings()
    org_flag = org_demo if org_demo is not None else (bool(organization.demo_mode) if organization else False)
    return bool(org_flag or settings.demo_mode)


def label_metrics_source(*, org_demo: bool, row_sources: set[str]) -> str:
    """
    Aggregate label for KPI/analytics surfaces.
    - demo: org in demo OR only demo rows
    - live: org live AND only live rows (or no rows)
    - mixed: org live but demo seed rows still present
    """
    if org_demo:
        return "demo"
    cleaned = {s for s in row_sources if s}
    if not cleaned:
        return "live"  # empty — not inventing demo metrics
    if cleaned == {"live"}:
        return "live"
    if cleaned == {"demo"}:
        return "mixed"  # seed still in DB while org claims live
    return "mixed"
