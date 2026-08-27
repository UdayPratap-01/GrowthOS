"""Read-only provider state reconciliation for ambiguous ads mutations."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.automation.idempotency import sanitize_platform_response
from app.core.config import get_settings
from app.integrations.google_oauth import ensure_access_token
from app.integrations.meta_family import META_GRAPH
from app.integrations.persistence import get_integration_row, load_tokens
from app.models.automation import AIAction
from app.models.enums import AIActionType
from app.models.marketing import Campaign
from app.publishing.ads_executor import _campaign_external_id
from app.publishing.provider_errors import ReconciliationOutcome


@dataclass
class ReconciliationResult:
    outcome: ReconciliationOutcome
    message: str
    provider: str
    operation: str
    external_id: str | None = None
    observed_state: dict[str, Any] = field(default_factory=dict)
    platform_response: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "outcome": self.outcome.value,
            "message": self.message,
            "provider": self.provider,
            "operation": self.operation,
            "external_id": self.external_id,
            "observed_state": self.observed_state,
            "platform_response": self.platform_response,
        }


RECONCILABLE_META = {
    AIActionType.pause_campaign,
    AIActionType.resume_campaign,
    AIActionType.update_budget,
}

RECONCILABLE_GOOGLE = {
    AIActionType.pause_campaign,
    AIActionType.resume_campaign,
}


class AdsReconciler:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def reconcile(self, action: AIAction, *, campaign: Campaign | None) -> ReconciliationResult:
        platform = (action.platform or "").lower()
        provider = "meta" if platform in {"meta", "facebook", "instagram"} else platform
        if provider in {"google", "google_ads"}:
            provider = "google_ads"

        operation = action.action_type.value
        if action.demo_mode:
            return ReconciliationResult(
                outcome=ReconciliationOutcome.unsupported,
                message="Demo actions are not reconciled against live providers",
                provider=provider,
                operation=operation,
            )

        if provider == "meta":
            if action.action_type not in RECONCILABLE_META:
                return ReconciliationResult(
                    outcome=ReconciliationOutcome.unsupported,
                    message=f"Meta reconciliation not supported for {operation}",
                    provider=provider,
                    operation=operation,
                )
            return await self._reconcile_meta(action, campaign=campaign)

        if provider == "google_ads":
            if action.action_type not in RECONCILABLE_GOOGLE:
                return ReconciliationResult(
                    outcome=ReconciliationOutcome.unsupported,
                    message=f"Google Ads reconciliation not supported for {operation}",
                    provider=provider,
                    operation=operation,
                )
            return await self._reconcile_google_ads(action, campaign=campaign)

        return ReconciliationResult(
            outcome=ReconciliationOutcome.unsupported,
            message=f"Reconciliation not supported for provider {provider!r}",
            provider=provider,
            operation=operation,
        )

    async def _reconcile_meta(
        self, action: AIAction, *, campaign: Campaign | None
    ) -> ReconciliationResult:
        operation = action.action_type.value
        if not campaign:
            return ReconciliationResult(
                outcome=ReconciliationOutcome.unknown,
                message="Campaign target missing for reconciliation",
                provider="meta",
                operation=operation,
            )
        external_id = _campaign_external_id(campaign)
        if not external_id:
            return ReconciliationResult(
                outcome=ReconciliationOutcome.unknown,
                message="No external_id available for Meta reconciliation",
                provider="meta",
                operation=operation,
                external_id=None,
            )

        row = await get_integration_row(
            self.db, organization_id=action.organization_id, provider="meta", client_id=action.client_id
        )
        if not row or not row.secret_ref:
            row = await get_integration_row(
                self.db, organization_id=action.organization_id, provider="meta", client_id=None
            )
        access_token = None
        if row:
            try:
                from app.integrations.meta_oauth import ensure_meta_access_token

                access_token = await ensure_meta_access_token(
                    self.db,
                    row,
                    organization_id=action.organization_id,
                    client_id=action.client_id,
                )
            except Exception:
                tokens = load_tokens(row) or {}
                access_token = tokens.get("access_token")
        if not access_token:
            return ReconciliationResult(
                outcome=ReconciliationOutcome.unknown,
                message="Meta credentials unavailable for reconciliation",
                provider="meta",
                operation=operation,
                external_id=external_id,
            )

        fields = "status,effective_status,daily_budget"
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.get(
                    f"{META_GRAPH}/{external_id}",
                    params={"fields": fields, "access_token": access_token},
                )
        except httpx.TimeoutException:
            return ReconciliationResult(
                outcome=ReconciliationOutcome.unknown,
                message="Meta status lookup timed out",
                provider="meta",
                operation=operation,
                external_id=external_id,
            )
        except httpx.HTTPError as exc:
            return ReconciliationResult(
                outcome=ReconciliationOutcome.unknown,
                message=f"Meta status lookup failed: {str(exc)[:200]}",
                provider="meta",
                operation=operation,
                external_id=external_id,
            )

        body = resp.json() if resp.content else {}
        clean = sanitize_platform_response(body if isinstance(body, dict) else {"body": body})
        if resp.status_code >= 400:
            return ReconciliationResult(
                outcome=ReconciliationOutcome.unknown,
                message="Meta status lookup returned an error",
                provider="meta",
                operation=operation,
                external_id=external_id,
                platform_response={"status_code": resp.status_code, "body": clean},
            )

        status = str(body.get("status") or body.get("effective_status") or "").upper()
        observed = {
            "status": status,
            "daily_budget": body.get("daily_budget"),
        }

        if action.action_type == AIActionType.pause_campaign:
            if status == "PAUSED":
                return ReconciliationResult(
                    outcome=ReconciliationOutcome.confirmed_success,
                    message="Meta campaign is PAUSED — mutation confirmed",
                    provider="meta",
                    operation=operation,
                    external_id=external_id,
                    observed_state=observed,
                    platform_response=clean,
                )
            if status in {"ACTIVE", "ENABLED"}:
                return ReconciliationResult(
                    outcome=ReconciliationOutcome.confirmed_not_applied,
                    message="Meta campaign is still ACTIVE — mutation not applied",
                    provider="meta",
                    operation=operation,
                    external_id=external_id,
                    observed_state=observed,
                    platform_response=clean,
                )

        elif action.action_type == AIActionType.resume_campaign:
            if status in {"ACTIVE", "ENABLED"}:
                return ReconciliationResult(
                    outcome=ReconciliationOutcome.confirmed_success,
                    message="Meta campaign is ACTIVE — mutation confirmed",
                    provider="meta",
                    operation=operation,
                    external_id=external_id,
                    observed_state=observed,
                    platform_response=clean,
                )
            if status == "PAUSED":
                return ReconciliationResult(
                    outcome=ReconciliationOutcome.confirmed_not_applied,
                    message="Meta campaign is still PAUSED — mutation not applied",
                    provider="meta",
                    operation=operation,
                    external_id=external_id,
                    observed_state=observed,
                    platform_response=clean,
                )

        elif action.action_type == AIActionType.update_budget:
            expected = (action.payload or {}).get("daily_budget") or campaign.daily_budget
            if expected is None:
                return ReconciliationResult(
                    outcome=ReconciliationOutcome.unknown,
                    message="Expected budget unknown — cannot reconcile",
                    provider="meta",
                    operation=operation,
                    external_id=external_id,
                    observed_state=observed,
                    platform_response=clean,
                )
            try:
                expected_minor = int(Decimal(str(expected)) * 100)
                observed_minor = int(body.get("daily_budget") or 0)
            except (TypeError, ValueError):
                return ReconciliationResult(
                    outcome=ReconciliationOutcome.unknown,
                    message="Could not compare Meta daily_budget values",
                    provider="meta",
                    operation=operation,
                    external_id=external_id,
                    observed_state=observed,
                    platform_response=clean,
                )
            if observed_minor == expected_minor:
                return ReconciliationResult(
                    outcome=ReconciliationOutcome.confirmed_success,
                    message="Meta daily_budget matches expected value",
                    provider="meta",
                    operation=operation,
                    external_id=external_id,
                    observed_state=observed,
                    platform_response=clean,
                )
            return ReconciliationResult(
                outcome=ReconciliationOutcome.confirmed_not_applied,
                message="Meta daily_budget does not match expected value",
                provider="meta",
                operation=operation,
                external_id=external_id,
                observed_state=observed,
                platform_response=clean,
            )

        return ReconciliationResult(
            outcome=ReconciliationOutcome.unknown,
            message=f"Unrecognized Meta status {status!r}",
            provider="meta",
            operation=operation,
            external_id=external_id,
            observed_state=observed,
            platform_response=clean,
        )

    async def _reconcile_google_ads(
        self, action: AIAction, *, campaign: Campaign | None
    ) -> ReconciliationResult:
        settings = get_settings()
        operation = action.action_type.value
        if not campaign:
            return ReconciliationResult(
                outcome=ReconciliationOutcome.unknown,
                message="Campaign target missing for reconciliation",
                provider="google_ads",
                operation=operation,
            )

        resource_id = _campaign_external_id(campaign)
        if not resource_id:
            return ReconciliationResult(
                outcome=ReconciliationOutcome.unknown,
                message="No external_id available for Google Ads reconciliation",
                provider="google_ads",
                operation=operation,
            )

        row = await get_integration_row(
            self.db, organization_id=action.organization_id, provider="google_ads", client_id=action.client_id
        )
        if not row or not row.secret_ref:
            row = await get_integration_row(
                self.db, organization_id=action.organization_id, provider="google_ads", client_id=None
            )
        if not row or not row.secret_ref:
            return ReconciliationResult(
                outcome=ReconciliationOutcome.unknown,
                message="Google Ads credentials unavailable",
                provider="google_ads",
                operation=operation,
                external_id=resource_id,
            )

        try:
            access_token = await ensure_access_token(
                self.db,
                row,
                organization_id=action.organization_id,
                provider="google_ads",
                client_id=action.client_id,
            )
        except Exception as exc:
            return ReconciliationResult(
                outcome=ReconciliationOutcome.unknown,
                message=f"Google Ads token unavailable: {str(exc)[:200]}",
                provider="google_ads",
                operation=operation,
                external_id=resource_id,
            )

        from app.integrations.google_ads_discovery import resolve_google_customer_id

        customer_id = resolve_google_customer_id(
            campaign_metrics=campaign.metrics if campaign else None,
            integration_config=(getattr(row, "config", None) or {}) if row else None,
            login_customer_id=settings.google_ads_login_customer_id,
        )
        if not customer_id:
            return ReconciliationResult(
                outcome=ReconciliationOutcome.unknown,
                message="Google Ads customer id not configured",
                provider="google_ads",
                operation=operation,
                external_id=resource_id,
            )

        customer_clean = str(customer_id).replace("-", "")
        resource_name = (
            resource_id
            if resource_id.startswith("customers/")
            else f"customers/{customer_clean}/campaigns/{resource_id}"
        )
        campaign_id = resource_name.split("/")[-1]
        query = f"""
            SELECT campaign.status
            FROM campaign
            WHERE campaign.id = {campaign_id}
        """
        headers = {
            "Authorization": f"Bearer {access_token}",
            "developer-token": settings.google_ads_developer_token or "",
            "Content-Type": "application/json",
        }
        if settings.google_ads_login_customer_id:
            headers["login-customer-id"] = settings.google_ads_login_customer_id.replace("-", "")

        url = f"https://googleads.googleapis.com/v18/customers/{customer_clean}/googleAds:search"
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.post(url, headers=headers, json={"query": query})
        except httpx.TimeoutException:
            return ReconciliationResult(
                outcome=ReconciliationOutcome.unknown,
                message="Google Ads status lookup timed out",
                provider="google_ads",
                operation=operation,
                external_id=resource_id,
            )
        except httpx.HTTPError as exc:
            return ReconciliationResult(
                outcome=ReconciliationOutcome.unknown,
                message=f"Google Ads status lookup failed: {str(exc)[:200]}",
                provider="google_ads",
                operation=operation,
                external_id=resource_id,
            )

        body = resp.json() if resp.content else {}
        clean = sanitize_platform_response(body if isinstance(body, dict) else {"body": body})
        if resp.status_code >= 400:
            return ReconciliationResult(
                outcome=ReconciliationOutcome.unknown,
                message="Google Ads status lookup returned an error",
                provider="google_ads",
                operation=operation,
                external_id=resource_id,
                platform_response={"status_code": resp.status_code, "body": clean},
            )

        results = body.get("results") or []
        if not results:
            return ReconciliationResult(
                outcome=ReconciliationOutcome.unknown,
                message="Google Ads campaign not found for reconciliation",
                provider="google_ads",
                operation=operation,
                external_id=resource_id,
                platform_response=clean,
            )

        status = str((results[0].get("campaign") or {}).get("status") or "").upper()
        observed = {"status": status}

        if action.action_type == AIActionType.pause_campaign:
            if status == "PAUSED":
                return ReconciliationResult(
                    outcome=ReconciliationOutcome.confirmed_success,
                    message="Google Ads campaign is PAUSED",
                    provider="google_ads",
                    operation=operation,
                    external_id=resource_id,
                    observed_state=observed,
                    platform_response=clean,
                )
            if status == "ENABLED":
                return ReconciliationResult(
                    outcome=ReconciliationOutcome.confirmed_not_applied,
                    message="Google Ads campaign is still ENABLED",
                    provider="google_ads",
                    operation=operation,
                    external_id=resource_id,
                    observed_state=observed,
                    platform_response=clean,
                )

        elif action.action_type == AIActionType.resume_campaign:
            if status == "ENABLED":
                return ReconciliationResult(
                    outcome=ReconciliationOutcome.confirmed_success,
                    message="Google Ads campaign is ENABLED",
                    provider="google_ads",
                    operation=operation,
                    external_id=resource_id,
                    observed_state=observed,
                    platform_response=clean,
                )
            if status == "PAUSED":
                return ReconciliationResult(
                    outcome=ReconciliationOutcome.confirmed_not_applied,
                    message="Google Ads campaign is still PAUSED",
                    provider="google_ads",
                    operation=operation,
                    external_id=resource_id,
                    observed_state=observed,
                    platform_response=clean,
                )

        return ReconciliationResult(
            outcome=ReconciliationOutcome.unknown,
            message=f"Unrecognized Google Ads status {status!r}",
            provider="google_ads",
            operation=operation,
            external_id=resource_id,
            observed_state=observed,
            platform_response=clean,
        )
