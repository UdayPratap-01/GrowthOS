from __future__ import annotations

from decimal import Decimal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.automation import AutonomySettings
from app.models.enums import AutonomyMode, AIActionType
from app.schemas.autopilot import AutonomySettingsOut, AutonomySettingsUpdate


DEFAULT_ALLOWED_ACTIONS = [a.value for a in AIActionType]
DEFAULT_PLATFORMS = ["meta", "instagram", "whatsapp", "google_ads", "youtube"]

_NUMERIC_CAPS = (
    "maximum_daily_ad_spend",
    "maximum_campaign_budget",
    "maximum_budget_increase_percentage",
    "maximum_budget_decrease_percentage",
)
_INT_CAPS = (
    "maximum_campaigns_per_day",
    "maximum_creatives_per_day",
    "maximum_posts_per_day",
    "maximum_actions_per_day",
    "max_ai_iterations",
    "max_ai_actions_per_cycle",
    "max_execution_time",
    "max_failures_per_cycle",
)


class AutonomyService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def get_or_create(self, organization_id: UUID, client_id: UUID | None = None) -> AutonomySettings:
        stmt = select(AutonomySettings).where(AutonomySettings.organization_id == organization_id)
        if client_id is None:
            stmt = stmt.where(AutonomySettings.client_id.is_(None))
        else:
            stmt = stmt.where(AutonomySettings.client_id == client_id)
        row = await self.db.scalar(stmt.limit(1))
        if row:
            # Keep allowlists current when new action types ship
            current = list(row.allowed_actions or [])
            missing = [a for a in DEFAULT_ALLOWED_ACTIONS if a not in current]
            if missing and current:
                row.allowed_actions = current + missing
                await self.db.flush()
            elif not current:
                row.allowed_actions = list(DEFAULT_ALLOWED_ACTIONS)
                await self.db.flush()
            return row
        row = AutonomySettings(
            organization_id=organization_id,
            client_id=client_id,
            autonomy_mode=AutonomyMode.copilot,
            allowed_platforms=list(DEFAULT_PLATFORMS),
            allowed_actions=list(DEFAULT_ALLOWED_ACTIONS),
            automation_enabled=False,
            maximum_daily_ad_spend=Decimal("500"),
            maximum_campaign_budget=Decimal("2000"),
            maximum_actions_per_day=50,
            max_ai_iterations=1,
            max_ai_actions_per_cycle=5,
            max_execution_time=300,
            max_failures_per_cycle=3,
        )
        self.db.add(row)
        await self.db.flush()
        await self.db.refresh(row)
        return row

    async def get_effective(self, organization_id: UUID, client_id: UUID | None = None) -> AutonomySettings:
        """Org defaults with optional client overrides, never exceeding org safety caps."""
        org = await self.get_or_create(organization_id, None)
        if client_id is None:
            return org
        client_row = await self.db.scalar(
            select(AutonomySettings)
            .where(
                AutonomySettings.organization_id == organization_id,
                AutonomySettings.client_id == client_id,
            )
            .limit(1)
        )
        if not client_row:
            return org
        # Build a transient merged view (do not persist merge)
        merged = AutonomySettings(
            organization_id=organization_id,
            client_id=client_id,
            autonomy_mode=client_row.autonomy_mode or org.autonomy_mode,
            automation_enabled=client_row.automation_enabled if client_row.automation_enabled is not None else org.automation_enabled,
            require_approval_for_financial_actions=org.require_approval_for_financial_actions
            or client_row.require_approval_for_financial_actions,
            require_approval_for_publishing=org.require_approval_for_publishing
            or client_row.require_approval_for_publishing,
            require_approval_for_campaign_creation=org.require_approval_for_campaign_creation
            or client_row.require_approval_for_campaign_creation,
            allowed_platforms=self._intersect_lists(org.allowed_platforms, client_row.allowed_platforms),
            allowed_actions=self._intersect_lists(org.allowed_actions, client_row.allowed_actions),
        )
        for key in _NUMERIC_CAPS + _INT_CAPS:
            org_val = getattr(org, key)
            client_val = getattr(client_row, key)
            if client_val is None:
                setattr(merged, key, org_val)
            else:
                try:
                    setattr(merged, key, min(org_val, client_val))
                except TypeError:
                    setattr(merged, key, org_val)
        # Detach: not added to session — used only for validation
        merged.id = client_row.id
        return merged

    @staticmethod
    def _intersect_lists(org_list: list | None, client_list: list | None) -> list:
        org_list = list(org_list or [])
        client_list = list(client_list or [])
        if not client_list:
            return org_list
        if not org_list:
            return client_list
        return [x for x in client_list if x in org_list]

    async def get_out(self, organization_id: UUID, client_id: UUID | None = None) -> AutonomySettingsOut:
        if client_id:
            # Prefer stored client row for editing; fall back to org
            row = await self.db.scalar(
                select(AutonomySettings)
                .where(
                    AutonomySettings.organization_id == organization_id,
                    AutonomySettings.client_id == client_id,
                )
                .limit(1)
            )
            if row:
                return AutonomySettingsOut.model_validate(row)
        row = await self.get_or_create(organization_id, None)
        return AutonomySettingsOut.model_validate(row)

    async def update(
        self, organization_id: UUID, data: AutonomySettingsUpdate, client_id: UUID | None = None
    ) -> AutonomySettingsOut:
        row = await self.get_or_create(organization_id, client_id)
        payload = data.model_dump(exclude_unset=True)
        if client_id is not None:
            org = await self.get_or_create(organization_id, None)
            for key in list(payload.keys()):
                if key in _NUMERIC_CAPS or key in _INT_CAPS:
                    org_val = getattr(org, key)
                    try:
                        if payload[key] is not None and org_val is not None and payload[key] > org_val:
                            payload[key] = org_val
                    except TypeError:
                        pass
                if key.startswith("require_approval_") and payload[key] is False and getattr(org, key):
                    # Client cannot disable an org-required approval
                    payload[key] = True
        for key, value in payload.items():
            setattr(row, key, value)
        await self.db.flush()
        await self.db.refresh(row)
        return AutonomySettingsOut.model_validate(row)
