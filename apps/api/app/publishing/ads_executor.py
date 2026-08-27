"""Ads platform execution — real API calls only when configured; never fake success."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any
from uuid import UUID

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.mode import ExecutionMode, effective_demo_mode
from app.integrations.google_oauth import ensure_access_token
from app.integrations.meta_family import META_GRAPH
from app.integrations.meta_oauth import ensure_meta_access_token
from app.integrations.persistence import get_integration_row, load_tokens
from app.models.automation import AIAction
from app.models.enums import AIActionType
from app.models.marketing import Campaign
from app.models.organization import Organization
from app.publishing.base import PublishResult
from app.publishing.provider_errors import (
    PROVIDER_TIMEOUT_AMBIGUOUS,
    PROVIDER_TRANSPORT_AMBIGUOUS,
    classify_google_ads_error,
    classify_meta_graph_error,
)


@dataclass
class AdsExecutionResult:
    success: bool
    status: str
    message: str
    external_id: str | None = None
    error: str | None = None
    error_code: str | None = None
    demo: bool = False
    ambiguous: bool = False
    execution_mode: str = ExecutionMode.real_execution.value
    platform_response: dict[str, Any] = field(default_factory=dict)
    before_state: dict[str, Any] = field(default_factory=dict)
    after_state: dict[str, Any] = field(default_factory=dict)
    started_at: str | None = None
    completed_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "confirmed": self.success,
            "demo": self.demo,
            "status": self.status,
            "message": self.message,
            "external_id": self.external_id,
            "error": self.error,
            "error_code": self.error_code,
            "ambiguous": self.ambiguous,
            "execution_mode": self.execution_mode,
            "platform_response": self.platform_response,
            "before_state": self.before_state,
            "after_state": self.after_state,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
        }


def _campaign_external_id(campaign: Campaign) -> str | None:
    if campaign.external_id:
        return campaign.external_id
    metrics = campaign.metrics or {}
    ext = metrics.get("external_campaign_id")
    return str(ext) if ext else None


class AdsExecutor:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def execute(self, action: AIAction, *, campaign: Campaign | None) -> AdsExecutionResult:
        started = datetime.now(timezone.utc).isoformat()
        settings = get_settings()
        org = await self.db.get(Organization, action.organization_id)
        live_demo_blocked = bool(
            settings.demo_mode or action.demo_mode or (org is not None and effective_demo_mode(org))
        )

        if live_demo_blocked:
            return await self._demo_execute(action, campaign=campaign, started_at=started)

        platform = (action.platform or "").lower()
        provider = "meta" if platform in {"meta", "facebook", "instagram"} else platform
        if provider in {"google", "google_ads"}:
            provider = "google_ads"

        row = await get_integration_row(
            self.db, organization_id=action.organization_id, provider=provider, client_id=action.client_id
        )
        if not row or not row.secret_ref or row.status != "connected":
            row = await get_integration_row(
                self.db, organization_id=action.organization_id, provider=provider, client_id=None
            )
        if not row or not row.secret_ref or row.status != "connected":
            label = provider.upper().replace("_", " ")
            return AdsExecutionResult(
                success=False,
                status="not_connected",
                message=f"{label} NOT CONNECTED",
                error=f"{label} NOT CONNECTED",
                error_code="INTEGRATION_NOT_CONNECTED",
                started_at=started,
                completed_at=datetime.now(timezone.utc).isoformat(),
            )

        if provider == "meta":
            return await self._execute_meta(action, campaign=campaign, started_at=started)
        if provider == "google_ads":
            return await self._execute_google_ads(action, campaign=campaign, started_at=started)

        return AdsExecutionResult(
            success=False,
            status="unsupported",
            message=f"Ads execution not supported for platform {platform!r}",
            error="UNSUPPORTED_OPERATION",
            error_code="UNSUPPORTED_OPERATION",
            started_at=started,
            completed_at=datetime.now(timezone.utc).isoformat(),
        )

    async def _demo_execute(
        self, action: AIAction, *, campaign: Campaign | None, started_at: str
    ) -> AdsExecutionResult:
        before: dict[str, Any] = {}
        after: dict[str, Any] = {}
        if campaign and action.action_type == AIActionType.pause_campaign:
            before = {"status": campaign.status}
            campaign.status = "paused"
            after = {"status": campaign.status}
        elif campaign and action.action_type == AIActionType.resume_campaign:
            before = {"status": campaign.status}
            campaign.status = "active"
            after = {"status": campaign.status}
        return AdsExecutionResult(
            success=True,
            status="demo_simulated",
            message="DEMO EXECUTION — ads mutation simulated locally; no live platform write.",
            demo=True,
            execution_mode=ExecutionMode.demo_execution.value,
            before_state=before,
            after_state=after,
            started_at=started_at,
            completed_at=datetime.now(timezone.utc).isoformat(),
        )

    async def _execute_meta(
        self, action: AIAction, *, campaign: Campaign | None, started_at: str
    ) -> AdsExecutionResult:
        row = await get_integration_row(
            self.db, organization_id=action.organization_id, provider="meta", client_id=action.client_id
        )
        if not row or not row.secret_ref:
            row = await get_integration_row(
                self.db, organization_id=action.organization_id, provider="meta", client_id=None
            )
        tokens = load_tokens(row) if row else None
        try:
            access_token = await ensure_meta_access_token(
                self.db,
                row,
                organization_id=action.organization_id,
                client_id=action.client_id,
            ) if row else None
        except Exception:
            access_token = (tokens or {}).get("access_token")
        if not access_token:
            return AdsExecutionResult(
                success=False,
                status="failed",
                message="Meta access token missing or expired",
                error="CREDENTIALS_EXPIRED",
                error_code="CREDENTIALS_EXPIRED",
                started_at=started_at,
                completed_at=datetime.now(timezone.utc).isoformat(),
            )

        if action.action_type in {AIActionType.create_campaign, AIActionType.create_ad_set, AIActionType.create_ad}:
            return AdsExecutionResult(
                success=False,
                status="not_implemented",
                message="Meta campaign/ad creation write adapter is not enabled in this release.",
                error="UNSUPPORTED_OPERATION",
                error_code="UNSUPPORTED_OPERATION",
                started_at=started_at,
                completed_at=datetime.now(timezone.utc).isoformat(),
            )

        if not campaign:
            return AdsExecutionResult(
                success=False,
                status="failed",
                message="Campaign target required for ads mutation",
                error="TARGET_NOT_FOUND",
                error_code="TARGET_NOT_FOUND",
                started_at=started_at,
                completed_at=datetime.now(timezone.utc).isoformat(),
            )

        external_id = _campaign_external_id(campaign)
        if not external_id:
            return AdsExecutionResult(
                success=False,
                status="failed",
                message="Campaign has no external_id — sync or publish must confirm platform id first",
                error="EXTERNAL_ID_REQUIRED",
                error_code="EXTERNAL_ID_REQUIRED",
                started_at=started_at,
                completed_at=datetime.now(timezone.utc).isoformat(),
            )

        before = {"status": campaign.status, "daily_budget": str(campaign.daily_budget) if campaign.daily_budget else None}
        params: dict[str, Any] = {"access_token": access_token}

        if action.action_type == AIActionType.pause_campaign:
            params["status"] = "PAUSED"
        elif action.action_type == AIActionType.resume_campaign:
            params["status"] = "ACTIVE"
        elif action.action_type == AIActionType.update_budget:
            daily = (action.payload or {}).get("daily_budget") or campaign.daily_budget
            if daily is None:
                return AdsExecutionResult(
                    success=False,
                    status="failed",
                    message="daily_budget required in payload or on campaign",
                    error="BUDGET_REQUIRED",
                    error_code="BUDGET_REQUIRED",
                    started_at=started_at,
                    completed_at=datetime.now(timezone.utc).isoformat(),
                )
            # Meta expects daily budget in account currency minor units (cents for USD).
            params["daily_budget"] = int(Decimal(str(daily)) * 100)
        else:
            return AdsExecutionResult(
                success=False,
                status="unsupported",
                message=f"Unsupported Meta ads action: {action.action_type.value}",
                error="UNSUPPORTED_OPERATION",
                error_code="UNSUPPORTED_OPERATION",
                started_at=started_at,
                completed_at=datetime.now(timezone.utc).isoformat(),
            )

        try:
            async with httpx.AsyncClient(timeout=60) as client:
                resp = await client.post(f"{META_GRAPH}/{external_id}", data=params)
        except httpx.TimeoutException:
            return AdsExecutionResult(
                success=False,
                status="ambiguous",
                message="Meta API request timed out — provider state unknown",
                error="Meta API request timed out",
                error_code=PROVIDER_TIMEOUT_AMBIGUOUS,
                ambiguous=True,
                external_id=external_id,
                started_at=started_at,
                completed_at=datetime.now(timezone.utc).isoformat(),
            )
        except httpx.HTTPError as exc:
            return AdsExecutionResult(
                success=False,
                status="ambiguous",
                message="Meta API network error — provider state unknown",
                error=str(exc)[:300],
                error_code=PROVIDER_TRANSPORT_AMBIGUOUS,
                ambiguous=True,
                external_id=external_id,
                started_at=started_at,
                completed_at=datetime.now(timezone.utc).isoformat(),
            )

        body = resp.json() if resp.content else {}
        body = {k: v for k, v in body.items() if k not in {"access_token", "refresh_token", "client_secret"}}
        if resp.status_code >= 400:
            error_code, _category = classify_meta_graph_error(
                status_code=resp.status_code, body=body if isinstance(body, dict) else {}, text=resp.text
            )
            err = body.get("error") if isinstance(body, dict) else {}
            err = err if isinstance(err, dict) else {}
            return AdsExecutionResult(
                success=False,
                status="failed",
                message=err.get("message") or resp.text[:300] or "Meta API error",
                error=err.get("message") or error_code,
                error_code=error_code,
                platform_response={"status_code": resp.status_code, "body": body},
                started_at=started_at,
                completed_at=datetime.now(timezone.utc).isoformat(),
            )

        if not body.get("success"):
            return AdsExecutionResult(
                success=False,
                status="failed",
                message="Meta did not confirm the mutation",
                error="EXECUTION_NOT_CONFIRMED",
                error_code="EXECUTION_NOT_CONFIRMED",
                platform_response=body,
                started_at=started_at,
                completed_at=datetime.now(timezone.utc).isoformat(),
            )

        after = dict(before)
        if action.action_type == AIActionType.pause_campaign:
            campaign.status = "paused"
            after["status"] = "paused"
        elif action.action_type == AIActionType.resume_campaign:
            campaign.status = "active"
            after["status"] = "active"
        elif action.action_type == AIActionType.update_budget:
            daily = (action.payload or {}).get("daily_budget") or campaign.daily_budget
            campaign.daily_budget = Decimal(str(daily))
            after["daily_budget"] = str(campaign.daily_budget)

        if not campaign.external_id:
            campaign.external_id = external_id

        return AdsExecutionResult(
            success=True,
            status="succeeded",
            message="Meta confirmed the ads mutation — post-action reconciliation still required",
            external_id=external_id,
            platform_response=body,
            before_state=before,
            after_state=after,
            started_at=started_at,
            completed_at=datetime.now(timezone.utc).isoformat(),
        )

    async def _execute_google_ads(
        self, action: AIAction, *, campaign: Campaign | None, started_at: str
    ) -> AdsExecutionResult:
        settings = get_settings()
        if action.action_type in {AIActionType.create_campaign, AIActionType.create_ad_set, AIActionType.create_ad}:
            return AdsExecutionResult(
                success=False,
                status="not_implemented",
                message="Google Ads campaign creation is not enabled in this release.",
                error="UNSUPPORTED_OPERATION",
                error_code="UNSUPPORTED_OPERATION",
                started_at=started_at,
                completed_at=datetime.now(timezone.utc).isoformat(),
            )

        if not campaign:
            return AdsExecutionResult(
                success=False,
                status="failed",
                message="Campaign target required",
                error="TARGET_NOT_FOUND",
                error_code="TARGET_NOT_FOUND",
                started_at=started_at,
                completed_at=datetime.now(timezone.utc).isoformat(),
            )

        resource_id = _campaign_external_id(campaign)
        if not resource_id:
            return AdsExecutionResult(
                success=False,
                status="failed",
                message="Campaign missing external_campaign_id from Google Ads sync",
                error="EXTERNAL_ID_REQUIRED",
                error_code="EXTERNAL_ID_REQUIRED",
                started_at=started_at,
                completed_at=datetime.now(timezone.utc).isoformat(),
            )

        if action.action_type == AIActionType.update_budget:
            return AdsExecutionResult(
                success=False,
                status="unsupported",
                message="Google Ads budget mutate adapter is not enabled in this release.",
                error="UNSUPPORTED_OPERATION",
                error_code="UNSUPPORTED_OPERATION",
                started_at=started_at,
                completed_at=datetime.now(timezone.utc).isoformat(),
            )

        if action.action_type not in {AIActionType.pause_campaign, AIActionType.resume_campaign}:
            return AdsExecutionResult(
                success=False,
                status="unsupported",
                message=f"Unsupported Google Ads action: {action.action_type.value}",
                error="UNSUPPORTED_OPERATION",
                error_code="UNSUPPORTED_OPERATION",
                started_at=started_at,
                completed_at=datetime.now(timezone.utc).isoformat(),
            )

        row = await get_integration_row(
            self.db, organization_id=action.organization_id, provider="google_ads", client_id=action.client_id
        )
        if not row or not row.secret_ref:
            row = await get_integration_row(
                self.db, organization_id=action.organization_id, provider="google_ads", client_id=None
            )
        if not row or not row.secret_ref:
            return AdsExecutionResult(
                success=False,
                status="not_connected",
                message="GOOGLE ADS NOT CONNECTED",
                error="GOOGLE ADS NOT CONNECTED",
                error_code="INTEGRATION_NOT_CONNECTED",
                started_at=started_at,
                completed_at=datetime.now(timezone.utc).isoformat(),
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
            return AdsExecutionResult(
                success=False,
                status="failed",
                message="Google Ads access token missing or expired",
                error=str(exc)[:300],
                error_code="CREDENTIALS_EXPIRED",
                started_at=started_at,
                completed_at=datetime.now(timezone.utc).isoformat(),
            )

        from app.integrations.google_ads_discovery import resolve_google_customer_id

        customer_id = resolve_google_customer_id(
            campaign_metrics=campaign.metrics if campaign else None,
            integration_config=row.config if row else None,
            login_customer_id=settings.google_ads_login_customer_id,
        )
        if not customer_id:
            return AdsExecutionResult(
                success=False,
                status="failed",
                message="Google Ads customer id not configured",
                error="NOT_CONFIGURED",
                error_code="NOT_CONFIGURED",
                started_at=started_at,
                completed_at=datetime.now(timezone.utc).isoformat(),
            )

        status_value = "PAUSED" if action.action_type == AIActionType.pause_campaign else "ENABLED"
        resource_name = (
            resource_id
            if resource_id.startswith("customers/")
            else f"customers/{customer_id}/campaigns/{resource_id}"
        )
        mutate_body = {
            "operations": [
                {
                    "update": {
                        "resourceName": resource_name,
                        "status": status_value,
                    },
                    "updateMask": "status",
                }
            ]
        }
        headers = {
            "Authorization": f"Bearer {access_token}",
            "developer-token": settings.google_ads_developer_token or "",
            "Content-Type": "application/json",
        }
        if settings.google_ads_login_customer_id:
            headers["login-customer-id"] = settings.google_ads_login_customer_id.replace("-", "")

        # Google Ads REST: campaign status updates use customers/{id}/campaigns:mutate
        url = f"https://googleads.googleapis.com/v18/customers/{customer_id}/campaigns:mutate"
        try:
            async with httpx.AsyncClient(timeout=60) as client:
                resp = await client.post(url, headers=headers, json=mutate_body)
        except httpx.TimeoutException:
            return AdsExecutionResult(
                success=False,
                status="ambiguous",
                message="Google Ads API request timed out — provider state unknown",
                error="Google Ads API request timed out",
                error_code=PROVIDER_TIMEOUT_AMBIGUOUS,
                ambiguous=True,
                external_id=resource_id,
                started_at=started_at,
                completed_at=datetime.now(timezone.utc).isoformat(),
            )
        except httpx.HTTPError as exc:
            return AdsExecutionResult(
                success=False,
                status="ambiguous",
                message="Google Ads API network error — provider state unknown",
                error=str(exc)[:300],
                error_code=PROVIDER_TRANSPORT_AMBIGUOUS,
                ambiguous=True,
                external_id=resource_id,
                started_at=started_at,
                completed_at=datetime.now(timezone.utc).isoformat(),
            )

        body = resp.json() if resp.content else {}
        if isinstance(body, dict):
            body = {k: v for k, v in body.items() if k not in {"access_token", "refresh_token", "developer-token"}}
        if resp.status_code >= 400:
            error_code, _cat = classify_google_ads_error(
                status_code=resp.status_code,
                body=body if isinstance(body, dict) else {},
                text=resp.text,
            )
            err = body.get("error") if isinstance(body, dict) else {}
            err = err if isinstance(err, dict) else {}
            return AdsExecutionResult(
                success=False,
                status="failed",
                message=err.get("message") or resp.text[:300] or "Google Ads API error",
                error=err.get("message") or error_code,
                error_code=error_code,
                platform_response={"status_code": resp.status_code, "body": body},
                started_at=started_at,
                completed_at=datetime.now(timezone.utc).isoformat(),
            )

        results = body.get("results") or []
        if not results:
            return AdsExecutionResult(
                success=False,
                status="failed",
                message="Google Ads did not confirm the mutation",
                error="EXECUTION_NOT_CONFIRMED",
                error_code="EXECUTION_NOT_CONFIRMED",
                platform_response=body,
                started_at=started_at,
                completed_at=datetime.now(timezone.utc).isoformat(),
            )

        before = {"status": campaign.status}
        campaign.status = "paused" if action.action_type == AIActionType.pause_campaign else "active"
        if not campaign.external_id:
            campaign.external_id = resource_id
        return AdsExecutionResult(
            success=True,
            status="succeeded",
            message="Google Ads confirmed the campaign status change — post-action reconciliation still required",
            external_id=resource_id,
            platform_response=body,
            before_state=before,
            after_state={"status": campaign.status},
            started_at=started_at,
            completed_at=datetime.now(timezone.utc).isoformat(),
        )


def ads_result_to_publish_result(result: AdsExecutionResult) -> PublishResult:
    return PublishResult(
        success=result.success,
        status=result.status,
        message=result.message,
        external_id=result.external_id,
        error=result.error,
        demo=result.demo,
        execution_mode=result.execution_mode,
        platform_response=result.platform_response,
        error_code=result.error_code,
        started_at=result.started_at,
        completed_at=result.completed_at,
    )
