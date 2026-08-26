"""Tenant ownership validation for AI actions and execution targets."""

from __future__ import annotations

from dataclasses import dataclass, field
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.automation import AIAction, CreativeAsset
from app.models.marketing import Ad, AdSet, Campaign


@dataclass
class TenantCheckResult:
    ok: bool
    errors: list[str] = field(default_factory=list)


class TargetValidator:
    """Verify action targets belong to the same organization (and client when set)."""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def validate_action_targets(self, action: AIAction) -> TenantCheckResult:
        errors: list[str] = []
        if not action.target_id:
            return TenantCheckResult(ok=True)

        target_uuid = self._parse_uuid(action.target_id)
        if target_uuid is None:
            # Opaque external ids (platform-side) are validated by the ads executor.
            return TenantCheckResult(ok=True)

        camp = await self._get_campaign(action.organization_id, target_uuid)
        if camp:
            return self._check_client(action, camp.client_id)

        ad_set = await self._get_ad_set(action.organization_id, target_uuid)
        if ad_set:
            err = self._check_client(action, ad_set.client_id)
            if not err.ok:
                return err
            return await self._campaign_client_matches(action, ad_set.campaign_id)

        ad = await self._get_ad(action.organization_id, target_uuid)
        if ad:
            err = self._check_client(action, ad.client_id)
            if not err.ok:
                return err
            ad_set_row = await self._get_ad_set(action.organization_id, ad.ad_set_id)
            if ad_set_row:
                return await self._campaign_client_matches(action, ad_set_row.campaign_id)
            return TenantCheckResult(ok=True)

        asset = await self.db.scalar(
            select(CreativeAsset).where(
                CreativeAsset.id == target_uuid,
                CreativeAsset.organization_id == action.organization_id,
            )
        )
        if asset:
            return self._check_client(action, asset.client_id)

        errors.append("TARGET_NOT_FOUND")
        return TenantCheckResult(ok=False, errors=errors)

    async def _campaign_client_matches(self, action: AIAction, campaign_id: UUID) -> TenantCheckResult:
        camp = await self._get_campaign(action.organization_id, campaign_id)
        if not camp:
            return TenantCheckResult(ok=False, errors=["TARGET_NOT_FOUND"])
        return self._check_client(action, camp.client_id)

    def _check_client(self, action: AIAction, resource_client_id: UUID) -> TenantCheckResult:
        if action.client_id is not None and action.client_id != resource_client_id:
            return TenantCheckResult(ok=False, errors=["TENANT_MISMATCH"])
        return TenantCheckResult(ok=True)

    async def _get_campaign(self, organization_id: UUID, campaign_id: UUID) -> Campaign | None:
        return await self.db.scalar(
            select(Campaign).where(Campaign.id == campaign_id, Campaign.organization_id == organization_id)
        )

    async def _get_ad_set(self, organization_id: UUID, ad_set_id: UUID) -> AdSet | None:
        return await self.db.scalar(
            select(AdSet).where(AdSet.id == ad_set_id, AdSet.organization_id == organization_id)
        )

    async def _get_ad(self, organization_id: UUID, ad_id: UUID) -> Ad | None:
        return await self.db.scalar(
            select(Ad).where(Ad.id == ad_id, Ad.organization_id == organization_id)
        )

    @staticmethod
    def _parse_uuid(value: str) -> UUID | None:
        try:
            return UUID(str(value))
        except (ValueError, TypeError):
            return None
