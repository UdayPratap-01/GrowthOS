"""Map existing AutonomyMode + flags onto Milestone 3 autonomy modes."""

from __future__ import annotations

from enum import Enum

from app.models.automation import AutonomySettings
from app.models.enums import AutonomyMode


class OptimizationAutonomyMode(str, Enum):
    """Product-facing modes for closed-loop optimization."""

    manual = "MANUAL"
    approval_required = "APPROVAL_REQUIRED"
    autonomous = "AUTONOMOUS"


def resolve_optimization_mode(settings: AutonomySettings) -> OptimizationAutonomyMode:
    """
    Safe mapping from stored autonomy settings.

    - automation_enabled=False OR copilot → MANUAL (recommendations only)
    - assisted OR autonomous with approval flags → APPROVAL_REQUIRED
    - autonomous + automation_enabled + financial approval not required → AUTONOMOUS
      (HIGH-risk actions are still forced to approval by the policy engine)
    """
    if not settings.automation_enabled:
        return OptimizationAutonomyMode.manual
    if settings.autonomy_mode == AutonomyMode.copilot:
        return OptimizationAutonomyMode.manual
    if settings.autonomy_mode == AutonomyMode.autonomous and not settings.require_approval_for_financial_actions:
        return OptimizationAutonomyMode.autonomous
    return OptimizationAutonomyMode.approval_required
