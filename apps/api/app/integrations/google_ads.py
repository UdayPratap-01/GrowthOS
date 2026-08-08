"""Google Ads OAuth + campaign/metrics sync (Phase 4)."""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from urllib.parse import urlencode
from uuid import UUID

import httpx
from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.integrations.base import (
    ConnectResult,
    ConnectionStatus,
    IntegrationConnectionStatus,
    MarketingIntegration,
    SyncResult,
)
from app.integrations.google_oauth import GOOGLE_AUTH, ensure_access_token, exchange_code
from app.integrations.oauth import decode_oauth_state, encode_oauth_state
from app.integrations.persistence import (
    clear_integration_secrets,
    get_integration_row,
    mark_sync,
    upsert_integration,
)
from app.models.enums import ConnectionStatus as ModelConnectionStatus
from app.models.enums import DataSource
from app.models.marketing import AdAccount, AnalyticsCampaign, AnalyticsDaily, Campaign

ADS_API = "https://googleads.googleapis.com/v18"
ADS_SCOPE = "https://www.googleapis.com/auth/adwords"


class GoogleAdsIntegration(MarketingIntegration):
    provider = "google_ads"
    display_name = "Google Ads"

    def credentials_configured(self) -> bool:
        settings = get_settings()
        return bool(
            settings.google_client_id
            and settings.google_client_secret
            and settings.google_ads_developer_token
        )

    def _redirect_uri(self) -> str:
        settings = get_settings()
        return (
            settings.google_ads_redirect_uri
            or f"{settings.api_public_url}/api/v1/integrations/google_ads/callback"
        )

    async def get_connection_status(self, organization_id: UUID, client_id: UUID | None = None) -> ConnectionStatus:
        db: AsyncSession = self._db  # type: ignore[attr-defined]
        row = await get_integration_row(
            db, organization_id=organization_id, provider=self.provider, client_id=client_id
        )
        configured = self.credentials_configured()
        if row and row.status == "connected" and row.secret_ref:
            cfg = row.config or {}
            return ConnectionStatus(
                provider=self.provider,
                status=IntegrationConnectionStatus.connected,
                message="Google Ads connected. Tokens stored encrypted server-side.",
                last_synced_at=cfg.get("last_synced_at"),
                account_label=cfg.get("account_label"),
                credentials_configured=configured,
                can_connect=False,
            )
        if row and row.status == "sync_error":
            cfg = row.config or {}
            return ConnectionStatus(
                provider=self.provider,
                status=IntegrationConnectionStatus.sync_error,
                message=cfg.get("last_sync_error") or "Last sync failed.",
                last_synced_at=cfg.get("last_synced_at"),
                account_label=cfg.get("account_label"),
                credentials_configured=configured,
                can_connect=False,
            )
        settings = get_settings()
        if settings.demo_mode and (not row or not row.secret_ref):
            return ConnectionStatus(
                provider=self.provider,
                status=IntegrationConnectionStatus.demo_data,
                message="Demo campaign data available. Live Google Ads is not connected.",
                credentials_configured=configured,
                can_connect=configured,
            )
        return ConnectionStatus(
            provider=self.provider,
            status=IntegrationConnectionStatus.not_connected,
            message=(
                "Not connected. Set GOOGLE_CLIENT_ID/SECRET and GOOGLE_ADS_DEVELOPER_TOKEN."
                if not configured
                else "Ready to connect Google Ads."
            ),
            credentials_configured=configured,
            can_connect=configured,
        )

    async def build_authorize_url(
        self, *, organization_id: UUID, user_id: UUID, client_id: UUID | None
    ) -> ConnectResult:
        if not self.credentials_configured():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    "Google Ads credentials not configured. Set GOOGLE_CLIENT_ID, "
                    "GOOGLE_CLIENT_SECRET, and GOOGLE_ADS_DEVELOPER_TOKEN."
                ),
            )
        settings = get_settings()
        state = encode_oauth_state(
            provider=self.provider,
            organization_id=organization_id,
            client_id=client_id,
            user_id=user_id,
        )
        params = {
            "client_id": settings.google_client_id,
            "redirect_uri": self._redirect_uri(),
            "response_type": "code",
            "scope": " ".join([ADS_SCOPE, "openid", "email"]),
            "access_type": "offline",
            "prompt": "consent",
            "state": state,
        }
        return ConnectResult(
            provider=self.provider,
            authorize_url=f"{GOOGLE_AUTH}?{urlencode(params)}",
            message="Redirect the user to Google to authorize Ads access.",
        )

    async def handle_callback(self, *, code: str, state: str) -> dict:
        db: AsyncSession = self._db  # type: ignore[attr-defined]
        try:
            payload = decode_oauth_state(state)
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
        if payload.get("provider") != self.provider:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Provider mismatch")

        token_data = await exchange_code(code=code, redirect_uri=self._redirect_uri())
        access_token = token_data["access_token"]

        customer_id, account_label = await self._resolve_customer(access_token)

        org_id = UUID(payload["organization_id"])
        client_id = UUID(payload["client_id"]) if payload.get("client_id") else None
        await upsert_integration(
            db,
            organization_id=org_id,
            provider=self.provider,
            client_id=client_id,
            status="connected",
            config={
                "account_label": account_label,
                "customer_id": customer_id,
                "connected_at": datetime.now(timezone.utc).isoformat(),
            },
            token_payload={
                "access_token": access_token,
                "refresh_token": token_data.get("refresh_token"),
                "expires_in": token_data.get("expires_in"),
                "token_type": token_data.get("token_type", "Bearer"),
                "obtained_at": datetime.now(timezone.utc).isoformat(),
                "provider": self.provider,
            },
        )
        return {
            "provider": self.provider,
            "organization_id": str(org_id),
            "client_id": str(client_id) if client_id else None,
            "account_label": account_label,
        }

    async def disconnect(self, organization_id: UUID, client_id: UUID | None = None) -> ConnectionStatus:
        db: AsyncSession = self._db  # type: ignore[attr-defined]
        row = await get_integration_row(
            db, organization_id=organization_id, provider=self.provider, client_id=client_id
        )
        if row:
            await clear_integration_secrets(db, row)
        return await self.get_connection_status(organization_id, client_id)

    async def sync(self, organization_id: UUID, client_id: UUID | None = None) -> SyncResult:
        db: AsyncSession = self._db  # type: ignore[attr-defined]
        row = await get_integration_row(
            db, organization_id=organization_id, provider=self.provider, client_id=client_id
        )
        if not row or not row.secret_ref:
            status_now = await self.get_connection_status(organization_id, client_id)
            return SyncResult(
                provider=self.provider,
                success=False,
                status=status_now.status,
                message="Live sync requires a connected Google Ads account.",
                errors=["not_connected"],
            )
        if not self.credentials_configured():
            await mark_sync(db, row, status="sync_error", error="Developer token missing")
            return SyncResult(
                provider=self.provider,
                success=False,
                status=IntegrationConnectionStatus.sync_error,
                message="GOOGLE_ADS_DEVELOPER_TOKEN is required for live Ads API calls.",
                errors=["missing_developer_token"],
            )
        customer_id = (row.config or {}).get("customer_id")
        if not customer_id:
            await mark_sync(db, row, status="sync_error", error="No Ads customer id")
            return SyncResult(
                provider=self.provider,
                success=False,
                status=IntegrationConnectionStatus.sync_error,
                message="No accessible Google Ads customer on the connected account.",
                errors=["missing_customer"],
            )
        try:
            access_token = await ensure_access_token(
                db, row, organization_id=organization_id, provider=self.provider, client_id=client_id
            )
            records = await self._sync_ads(db, organization_id, client_id, access_token, str(customer_id))
            await mark_sync(db, row, status="connected", records_synced=records)
            return SyncResult(
                provider=self.provider,
                success=True,
                status=IntegrationConnectionStatus.connected,
                records_synced=records,
                message=f"Synced {records} Google Ads campaign/metric rows.",
            )
        except Exception as exc:
            await mark_sync(db, row, status="sync_error", error=str(exc))
            return SyncResult(
                provider=self.provider,
                success=False,
                status=IntegrationConnectionStatus.sync_error,
                message="Google Ads sync failed.",
                errors=[str(exc)],
            )

    async def _resolve_customer(self, access_token: str) -> tuple[str | None, str]:
        settings = get_settings()
        headers = {
            "Authorization": f"Bearer {access_token}",
            "developer-token": settings.google_ads_developer_token,
        }
        if settings.google_ads_login_customer_id:
            headers["login-customer-id"] = settings.google_ads_login_customer_id.replace("-", "")
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(f"{ADS_API}/customers:listAccessibleCustomers", headers=headers)
        if resp.status_code >= 400:
            # OAuth succeeded; account discovery can fail until developer token is approved.
            return None, "Google Ads (pending customer discovery)"
        names = resp.json().get("resourceNames") or []
        if not names:
            return None, "Google Ads (no accessible customers)"
        customer_id = str(names[0]).split("/")[-1]
        return customer_id, f"Google Ads / {customer_id}"

    async def _sync_ads(
        self,
        db: AsyncSession,
        organization_id: UUID,
        client_id: UUID | None,
        access_token: str,
        customer_id: str,
    ) -> int:
        settings = get_settings()
        headers = {
            "Authorization": f"Bearer {access_token}",
            "developer-token": settings.google_ads_developer_token,
            "Content-Type": "application/json",
        }
        if settings.google_ads_login_customer_id:
            headers["login-customer-id"] = settings.google_ads_login_customer_id.replace("-", "")

        query = """
            SELECT
              campaign.id,
              campaign.name,
              campaign.status,
              campaign.advertising_channel_type,
              segments.date,
              metrics.impressions,
              metrics.clicks,
              metrics.cost_micros,
              metrics.conversions
            FROM campaign
            WHERE segments.date DURING LAST_7_DAYS
        """
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(
                f"{ADS_API}/customers/{customer_id}/googleAds:search",
                headers=headers,
                json={"query": query},
            )
            if resp.status_code >= 400:
                raise RuntimeError(resp.text)
            results = resp.json().get("results") or []

        if not client_id:
            return len(results)

        ad_account = await self._upsert_ad_account(db, organization_id, client_id, customer_id)
        by_campaign: dict[str, dict] = {}
        daily_totals: dict[date, dict[str, Decimal | int]] = {}

        for item in results:
            campaign = item.get("campaign") or {}
            metrics = item.get("metrics") or {}
            segments = item.get("segments") or {}
            ext_id = str(campaign.get("id") or "")
            if not ext_id:
                continue
            day_raw = segments.get("date")
            day = date.fromisoformat(day_raw) if day_raw else date.today()
            impressions = int(metrics.get("impressions") or 0)
            clicks = int(metrics.get("clicks") or 0)
            cost_micros = Decimal(str(metrics.get("costMicros") or metrics.get("cost_micros") or 0))
            spend = (cost_micros / Decimal("1000000")).quantize(Decimal("0.01"))
            conversions = int(float(metrics.get("conversions") or 0))

            bucket = by_campaign.setdefault(
                ext_id,
                {
                    "name": campaign.get("name") or f"Campaign {ext_id}",
                    "status": str(campaign.get("status") or "UNKNOWN").lower(),
                    "objective": campaign.get("advertisingChannelType")
                    or campaign.get("advertising_channel_type"),
                    "spend": Decimal("0"),
                    "impressions": 0,
                    "clicks": 0,
                    "conversions": 0,
                    "days": [],
                },
            )
            bucket["spend"] += spend
            bucket["impressions"] += impressions
            bucket["clicks"] += clicks
            bucket["conversions"] += conversions
            bucket["days"].append(
                {
                    "date": day,
                    "spend": spend,
                    "impressions": impressions,
                    "clicks": clicks,
                    "conversions": conversions,
                }
            )

            day_tot = daily_totals.setdefault(
                day, {"spend": Decimal("0"), "impressions": 0, "clicks": 0, "conversions": 0}
            )
            day_tot["spend"] = Decimal(day_tot["spend"]) + spend
            day_tot["impressions"] = int(day_tot["impressions"]) + impressions
            day_tot["clicks"] = int(day_tot["clicks"]) + clicks
            day_tot["conversions"] = int(day_tot["conversions"]) + conversions

        count = 0
        for ext_id, data in by_campaign.items():
            camp = await self._upsert_campaign(
                db,
                organization_id=organization_id,
                client_id=client_id,
                ad_account_id=ad_account.id,
                external_id=ext_id,
                name=data["name"],
                status=data["status"],
                objective=data.get("objective"),
                spend=data["spend"],
                impressions=data["impressions"],
                clicks=data["clicks"],
                conversions=data["conversions"],
            )
            count += 1
            for day_row in data["days"]:
                leads = int(day_row["conversions"])
                spend = day_row["spend"]
                clicks = int(day_row["clicks"])
                impressions = int(day_row["impressions"])
                cpl = (spend / Decimal(leads)).quantize(Decimal("0.01")) if leads else None
                ctr = (
                    (Decimal(clicks) / Decimal(impressions) * Decimal("100")).quantize(Decimal("0.0001"))
                    if impressions
                    else None
                )
                db.add(
                    AnalyticsCampaign(
                        organization_id=organization_id,
                        client_id=client_id,
                        campaign_id=camp.id,
                        date=day_row["date"],
                        spend=spend,
                        leads=leads,
                        impressions=impressions,
                        clicks=clicks,
                        ctr=ctr,
                        cpl=cpl,
                        metrics={"source": "google_ads_live", "external_campaign_id": ext_id},
                        data_source=DataSource.live,
                    )
                )
                count += 1

        for day, tot in daily_totals.items():
            conversions = int(tot["conversions"])
            db.add(
                AnalyticsDaily(
                    organization_id=organization_id,
                    client_id=client_id,
                    date=day,
                    spend=Decimal(tot["spend"]),
                    leads=conversions,
                    revenue=Decimal("0"),
                    impressions=int(tot["impressions"]),
                    clicks=int(tot["clicks"]),
                    conversions=conversions,
                    metrics={"source": "google_ads_live", "provider": "google_ads", "customer_id": customer_id},
                    data_source=DataSource.live,
                )
            )
            count += 1

        await db.flush()
        return count

    async def _upsert_ad_account(
        self, db: AsyncSession, organization_id: UUID, client_id: UUID, customer_id: str
    ) -> AdAccount:
        row = await db.scalar(
            select(AdAccount).where(
                AdAccount.organization_id == organization_id,
                AdAccount.client_id == client_id,
                AdAccount.provider == "google_ads",
                AdAccount.external_id == customer_id,
            ).limit(1)
        )
        if row:
            row.connection_status = ModelConnectionStatus.connected
            row.last_synced_at = datetime.now(timezone.utc)
            row.name = f"Google Ads / {customer_id}"
            return row
        row = AdAccount(
            organization_id=organization_id,
            client_id=client_id,
            provider="google_ads",
            external_id=customer_id,
            name=f"Google Ads / {customer_id}",
            connection_status=ModelConnectionStatus.connected,
            encrypted_credentials_ref=None,
            meta={"source": "google_ads_live"},
            last_synced_at=datetime.now(timezone.utc),
        )
        db.add(row)
        await db.flush()
        return row

    async def _upsert_campaign(
        self,
        db: AsyncSession,
        *,
        organization_id: UUID,
        client_id: UUID,
        ad_account_id: UUID,
        external_id: str,
        name: str,
        status: str,
        objective: str | None,
        spend: Decimal,
        impressions: int,
        clicks: int,
        conversions: int,
    ) -> Campaign:
        rows = (
            await db.execute(
                select(Campaign).where(
                    Campaign.organization_id == organization_id,
                    Campaign.client_id == client_id,
                    Campaign.platform == "google_ads",
                )
            )
        ).scalars().all()
        existing = next(
            (c for c in rows if (c.metrics or {}).get("external_campaign_id") == external_id),
            None,
        )
        metrics = {
            "external_campaign_id": external_id,
            "impressions": impressions,
            "clicks": clicks,
            "conversions": conversions,
            "leads": conversions,
            "source": "google_ads_live",
        }
        if clicks and impressions:
            metrics["ctr"] = float((Decimal(clicks) / Decimal(impressions) * Decimal(100)).quantize(Decimal("0.01")))
        if conversions:
            metrics["cpl"] = float((spend / Decimal(conversions)).quantize(Decimal("0.01")))
        if existing:
            existing.name = name
            existing.status = status
            existing.objective = objective
            existing.spend = spend
            existing.metrics = metrics
            existing.ad_account_id = ad_account_id
            existing.data_source = DataSource.live
            return existing
        camp = Campaign(
            organization_id=organization_id,
            client_id=client_id,
            ad_account_id=ad_account_id,
            name=name,
            platform="google_ads",
            status=status,
            objective=objective,
            spend=spend,
            metrics=metrics,
            data_source=DataSource.live,
        )
        db.add(camp)
        await db.flush()
        return camp
