"""Bounded autopilot orchestration — explicit cycles, no infinite loops."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.automation import AutopilotRun
from app.schemas.autopilot import AutopilotCycleResult
from app.services.action_service import ActionService
from app.services.analytics_service import AnalyticsService
from app.services.autonomy_service import AutonomyService
from app.services.optimization_service import OptimizationService


class AutopilotOrchestratorService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def run_cycle(
        self,
        organization,
        *,
        client_id: UUID,
        run_id: UUID | None,
        user_id: UUID,
        max_iterations: int = 1,
    ) -> AutopilotCycleResult:
        """
        Execute one bounded monitor → optimize cycle for an existing autopilot run.

        Does not publish or spend autonomously; creates structured actions only.
        """
        settings = await AutonomyService(self.db).get_effective(organization.id, client_id)
        iterations = min(max_iterations, settings.max_ai_iterations or 1)
        cycle_id = str(uuid4())
        started = datetime.now(timezone.utc)

        run = None
        if run_id:
            run = await self.db.scalar(
                select(AutopilotRun).where(
                    AutopilotRun.id == run_id,
                    AutopilotRun.organization_id == organization.id,
                    AutopilotRun.client_id == client_id,
                )
            )

        actions_created = 0
        actions_executed = 0
        actions_blocked = 0
        errors: list[str] = []

        analytics = await AnalyticsService(self.db).get_analytics(
            organization.id, client_id=client_id, period_days=30, demo_mode=organization.demo_mode
        )
        if analytics.insufficient_data:
            errors.append("INSUFFICIENT_DATA")

        # Closed-loop: evaluate PerformanceRecommendations under policy (no duplicate execution path).
        from app.core.config import get_settings
        from app.optimization.closed_loop import ClosedLoopOptimizer

        closed_loop_summary = {"evaluated": 0, "actions": 0, "blocked": 0, "approval_required": 0, "no_action": 0}
        if get_settings().optimization_enabled:
            try:
                closed_loop_summary = await ClosedLoopOptimizer(self.db).process_client_recommendations(
                    organization_id=organization.id,
                    client_id=client_id,
                    actor_user_id=user_id,
                    limit=settings.max_ai_actions_per_cycle or 5,
                )
                actions_created += int(closed_loop_summary.get("actions") or 0)
                actions_blocked += int(closed_loop_summary.get("blocked") or 0) + int(
                    closed_loop_summary.get("approval_required") or 0
                )
            except Exception as exc:  # noqa: BLE001
                errors.append(f"CLOSED_LOOP:{str(exc)[:180]}")

        from app.schemas.autopilot import DecisionLoopRequest

        for i in range(iterations):
            try:
                result = await OptimizationService(self.db).run_decision_loop(
                    organization,
                    DecisionLoopRequest(client_id=client_id, max_actions=settings.max_ai_actions_per_cycle, max_iterations=1),
                    user_id=user_id,
                )
                actions_created += result.actions_created
            except Exception as exc:  # noqa: BLE001
                errors.append(str(exc)[:200])
                actions_blocked += 1
                break

        pending = await ActionService(self.db).list(
            organization.id, client_id=client_id, status_filter=None, limit=200
        )
        for action in pending:
            if action.status.value in {"PENDING", "APPROVED"}:
                actions_blocked += 1

        completed = datetime.now(timezone.utc)
        cycle_result = AutopilotCycleResult(
            cycle_id=cycle_id,
            organization_id=organization.id,
            client_id=client_id,
            run_id=run_id,
            started_at=started,
            completed_at=completed,
            iterations=iterations,
            actions_created=actions_created,
            actions_executed=actions_executed,
            actions_blocked=actions_blocked,
            errors=errors,
            analytics_data_source=analytics.data_source,
            message=(
                f"Cycle completed: {actions_created} actions created, "
                f"closed_loop={closed_loop_summary}, {len(errors)} errors"
            ),
        )

        if run:
            result_payload = dict(run.result or {})
            cycles = list(result_payload.get("cycles") or [])
            cycles.append(cycle_result.model_dump(mode="json"))
            result_payload["cycles"] = cycles[-10:]
            run.result = result_payload
            self._mark_step(run, "monitoring", "completed", f"Cycle {cycle_id} analytics reviewed")
            self._mark_step(run, "optimization", "completed" if actions_created else "blocked", cycle_result.message)
            await self.db.flush()

        return cycle_result

    @property
    def message(self) -> str:
        return "Autopilot cycle completed"

    def _mark_step(self, run: AutopilotRun, key: str, status: str, detail: str) -> None:
        steps = [dict(s) for s in (run.steps or [])]
        for step in steps:
            if step.get("key") == key:
                step["status"] = status
                step["detail"] = detail
                break
        run.steps = steps
