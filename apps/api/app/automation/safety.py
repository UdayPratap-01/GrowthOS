"""Permission, budget, autonomy, and rate-limit validators for AI actions."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.automation.action_types import (
    CAMPAIGN_CREATE_ACTIONS,
    FINANCIAL_ACTIONS,
    PUBLISH_ACTIONS,
    get_action_spec,
)
from app.models.automation import AIAction, AutonomySettings
from app.models.enums import AIActionStatus, AIActionType, AutonomyMode, RiskLevel

COST_REQUIRED_ACTIONS = {
    AIActionType.create_campaign,
    AIActionType.create_ad_set,
    AIActionType.create_ad,
    AIActionType.update_budget,
}

# Pending proposals do not burn daily quotas — only committed/executed work does.
RATE_LIMIT_STATUSES = (
    AIActionStatus.approved,
    AIActionStatus.executing,
    AIActionStatus.completed,
    AIActionStatus.scheduled,
)


@dataclass
class ValidationResult:
    ok: bool
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    requires_approval: bool = True


class PermissionChecker:
    async def check_tenant(self, organization_id: UUID, action_org_id: UUID) -> ValidationResult:
        if organization_id != action_org_id:
            return ValidationResult(ok=False, errors=["TENANT_MISMATCH"])
        return ValidationResult(ok=True, requires_approval=False)


class BudgetGuard:
    def __init__(self, settings: AutonomySettings) -> None:
        self.settings = settings

    def check_estimated_cost(self, estimated_cost: Decimal | None, action_type: AIActionType) -> ValidationResult:
        if action_type in COST_REQUIRED_ACTIONS and estimated_cost is None:
            return ValidationResult(
                ok=False,
                errors=["BUDGET_REQUIRED: this action requires estimated_cost"],
            )
        if estimated_cost is None:
            return ValidationResult(ok=True, warnings=["NO_ESTIMATED_COST"])
        if action_type in FINANCIAL_ACTIONS and estimated_cost > self.settings.maximum_campaign_budget:
            return ValidationResult(
                ok=False,
                errors=[
                    f"BUDGET_LIMIT: estimated_cost exceeds maximum_campaign_budget ({self.settings.maximum_campaign_budget})"
                ],
            )
        if estimated_cost > self.settings.maximum_daily_ad_spend:
            return ValidationResult(
                ok=False,
                errors=[
                    f"BUDGET_LIMIT: estimated_cost exceeds maximum_daily_ad_spend ({self.settings.maximum_daily_ad_spend})"
                ],
            )
        return ValidationResult(ok=True)

    def check_budget_delta_pct(self, pct_change: Decimal) -> ValidationResult:
        if pct_change > 0 and pct_change > self.settings.maximum_budget_increase_percentage:
            return ValidationResult(ok=False, errors=["BUDGET_INCREASE_LIMIT"])
        if pct_change < 0 and abs(pct_change) > self.settings.maximum_budget_decrease_percentage:
            return ValidationResult(ok=False, errors=["BUDGET_DECREASE_LIMIT"])
        return ValidationResult(ok=True)


class ActionValidator:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def validate(
        self,
        *,
        organization_id: UUID,
        settings: AutonomySettings,
        action_type: AIActionType,
        platform: str | None,
        estimated_cost: Decimal | None,
        client_id: UUID | None,
        payload: dict | None = None,
    ) -> ValidationResult:
        errors: list[str] = []
        warnings: list[str] = []

        if not settings.automation_enabled and action_type in FINANCIAL_ACTIONS | PUBLISH_ACTIONS:
            warnings.append("AUTOMATION_DISABLED")

        spec = get_action_spec(action_type)
        if spec.requires_platform and not platform:
            errors.append("PLATFORM_REQUIRED")

        allowed_platforms = settings.allowed_platforms or []
        if platform and allowed_platforms and platform not in allowed_platforms:
            errors.append(f"PLATFORM_NOT_ALLOWED: {platform}")

        allowed_actions = settings.allowed_actions or []
        if allowed_actions and action_type.value not in allowed_actions:
            errors.append(f"ACTION_NOT_ALLOWED: {action_type.value}")

        budget = BudgetGuard(settings).check_estimated_cost(estimated_cost, action_type)
        errors.extend(budget.errors)
        warnings.extend(budget.warnings)

        if action_type == AIActionType.update_budget:
            pct_raw = (payload or {}).get("budget_increase_pct") or (payload or {}).get("budget_change_pct")
            if pct_raw is not None:
                try:
                    pct = Decimal(str(pct_raw))
                    delta = BudgetGuard(settings).check_budget_delta_pct(pct)
                    errors.extend(delta.errors)
                except Exception:
                    errors.append("BUDGET_CHANGE_PCT_INVALID")

        start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
        if action_type in CAMPAIGN_CREATE_ACTIONS:
            count = await self._count_today(organization_id, client_id, CAMPAIGN_CREATE_ACTIONS, start)
            if count >= settings.maximum_campaigns_per_day:
                errors.append("RATE_LIMIT: maximum_campaigns_per_day")
        creative_types = {
            AIActionType.create_creative,
            AIActionType.generate_image,
            AIActionType.generate_video,
            AIActionType.generate_creative_variations,
        }
        if action_type in creative_types:
            count = await self._count_today(organization_id, client_id, creative_types, start)
            if count >= settings.maximum_creatives_per_day:
                errors.append("RATE_LIMIT: maximum_creatives_per_day")
        if action_type in PUBLISH_ACTIONS | {AIActionType.create_content}:
            count = await self._count_today(
                organization_id, client_id, PUBLISH_ACTIONS | {AIActionType.create_content}, start
            )
            if count >= settings.maximum_posts_per_day:
                errors.append("RATE_LIMIT: maximum_posts_per_day")

        max_actions = getattr(settings, "maximum_actions_per_day", None)
        if max_actions is not None:
            total_today = await self._count_all_today(organization_id, client_id, start)
            if total_today >= int(max_actions):
                errors.append("RATE_LIMIT: maximum_actions_per_day")

        requires_approval = self._requires_approval(settings, action_type)
        return ValidationResult(ok=not errors, errors=errors, warnings=warnings, requires_approval=requires_approval)

    def _requires_approval(self, settings: AutonomySettings, action_type: AIActionType) -> bool:
        if settings.autonomy_mode == AutonomyMode.copilot:
            return True
        if action_type in FINANCIAL_ACTIONS and settings.require_approval_for_financial_actions:
            return True
        if action_type in PUBLISH_ACTIONS and settings.require_approval_for_publishing:
            return True
        if action_type in CAMPAIGN_CREATE_ACTIONS and settings.require_approval_for_campaign_creation:
            return True
        if settings.autonomy_mode == AutonomyMode.assisted:
            return get_action_spec(action_type).default_risk in {RiskLevel.high, RiskLevel.critical}
        return False

    async def _count_today(
        self,
        organization_id: UUID,
        client_id: UUID | None,
        types: set[AIActionType],
        start: datetime,
    ) -> int:
        stmt = select(func.count()).select_from(AIAction).where(
            AIAction.organization_id == organization_id,
            AIAction.action_type.in_(list(types)),
            AIAction.created_at >= start,
            AIAction.status.in_(list(RATE_LIMIT_STATUSES)),
        )
        if client_id is not None:
            stmt = stmt.where(AIAction.client_id == client_id)
        return int(await self.db.scalar(stmt) or 0)

    async def _count_all_today(
        self,
        organization_id: UUID,
        client_id: UUID | None,
        start: datetime,
    ) -> int:
        stmt = select(func.count()).select_from(AIAction).where(
            AIAction.organization_id == organization_id,
            AIAction.created_at >= start,
            AIAction.status.in_(list(RATE_LIMIT_STATUSES)),
        )
        if client_id is not None:
            stmt = stmt.where(AIAction.client_id == client_id)
        return int(await self.db.scalar(stmt) or 0)
