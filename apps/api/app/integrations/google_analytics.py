"""Google Analytics 4 OAuth + Data API sync."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from urllib.parse import urlencode
from uuid import UUID

import httpx
from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.integrations.base import (
    ConnectResult,
    ConnectionStatus,
    IntegrationConnectionStatus,
    MarketingIntegration,
    SyncResult,
)
from app.integrations.oauth import decode_oauth_state, encode_oauth_state
from app.integrations.persistence import (
    clear_integration_secrets,
    get_integration_row,
    load_tokens,
    mark_sync,
    upsert_integration,
)
from app.models.enums import DataSource
from app.models.marketing import AnalyticsDaily

GOOGLE_AUTH = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN = "https://oauth2.googleapis.com/token"
GA_ADMIN = "https://analyticsadmin.googleapis.com/v1beta/accountSummaries"
GA_DATA = "https://analyticsdata.googleapis.com/v1beta"


class GoogleAnalyticsIntegration(MarketingIntegration):
    provider = "google_analytics"
    display_name = "Google Analytics"

    def credentials_configured(self) -> bool:
        settings = get_settings()
        return bool(settings.google_client_id and settings.google_client_secret)

    def _redirect_uri(self) -> str:
        settings = get_settings()
        return (
            settings.google_redirect_uri
            or f"{settings.api_public_url}/api/v1/integrations/google_analytics/callback"
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
                message="Google Analytics connected. Tokens stored encrypted server-side.",
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
                credentials_configured=configured,
                can_connect=False,
            )
        settings = get_settings()
        if settings.demo_mode and (not row or not row.secret_ref):
            return ConnectionStatus(
                provider=self.provider,
                status=IntegrationConnectionStatus.demo_data,
                message="Demo analytics available. Live Google Analytics is not connected.",
                credentials_configured=configured,
                can_connect=configured,
            )
        return ConnectionStatus(
            provider=self.provider,
            status=IntegrationConnectionStatus.not_connected,
            message="Not connected. Configure GOOGLE_CLIENT_ID/SECRET and complete OAuth."
            if not configured
            else "Ready to connect Google Analytics.",
            credentials_configured=configured,
            can_connect=configured,
        )

    async def build_authorize_url(
        self, *, organization_id: UUID, user_id: UUID, client_id: UUID | None
    ) -> ConnectResult:
        if not self.credentials_configured():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Google OAuth credentials not configured. Set GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET.",
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
            "scope": " ".join(
                [
                    "https://www.googleapis.com/auth/analytics.readonly",
                    "openid",
                    "email",
                ]
            ),
            "access_type": "offline",
            "prompt": "consent",
            "state": state,
        }
        return ConnectResult(
            provider=self.provider,
            authorize_url=f"{GOOGLE_AUTH}?{urlencode(params)}",
            message="Redirect the user to Google to authorize Analytics access.",
        )

    async def handle_callback(self, *, code: str, state: str) -> dict:
        db: AsyncSession = self._db  # type: ignore[attr-defined]
        settings = get_settings()
        try:
            payload = decode_oauth_state(state)
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
        if payload.get("provider") != self.provider:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Provider mismatch")

        async with httpx.AsyncClient(timeout=30) as client:
            token_resp = await client.post(
                GOOGLE_TOKEN,
                data={
                    "code": code,
                    "client_id": settings.google_client_id,
                    "client_secret": settings.google_client_secret,
                    "redirect_uri": self._redirect_uri(),
                    "grant_type": "authorization_code",
                },
            )
            if token_resp.status_code >= 400:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Google token exchange failed: {token_resp.text}",
                )
            token_data = token_resp.json()
            access_token = token_data.get("access_token")
            if not access_token:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No Google access token returned")

            accounts_resp = await client.get(
                GA_ADMIN,
                headers={"Authorization": f"Bearer {access_token}"},
            )
            account_label = "Google Analytics"
            property_id = None
            if accounts_resp.status_code < 400:
                summaries = accounts_resp.json().get("accountSummaries") or []
                if summaries:
                    account_label = summaries[0].get("displayName") or account_label
                    props = summaries[0].get("propertySummaries") or []
                    if props:
                        property_id = props[0].get("property")
                        account_label = f"{account_label} / {props[0].get('displayName', property_id)}"

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
                "property_id": property_id,
                "connected_at": datetime.now(timezone.utc).isoformat(),
            },
            token_payload={
                "access_token": access_token,
                "refresh_token": token_data.get("refresh_token"),
                "expires_in": token_data.get("expires_in"),
                "token_type": token_data.get("token_type", "Bearer"),
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
                message="Live sync requires a connected Google Analytics property.",
                errors=["not_connected"],
            )
        tokens = load_tokens(row)
        if not tokens or not tokens.get("access_token"):
            await mark_sync(db, row, status="sync_error", error="Token unavailable")
            return SyncResult(
                provider=self.provider,
                success=False,
                status=IntegrationConnectionStatus.sync_error,
                message="Stored Google credentials unavailable.",
                errors=["token_unavailable"],
            )
        property_id = (row.config or {}).get("property_id")
        if not property_id:
            await mark_sync(db, row, status="sync_error", error="No GA4 property selected")
            return SyncResult(
                provider=self.provider,
                success=False,
                status=IntegrationConnectionStatus.sync_error,
                message="No GA4 property available on the connected account.",
                errors=["missing_property"],
            )
        try:
            records = await self._sync_ga4(db, organization_id, client_id, tokens["access_token"], property_id)
            await mark_sync(db, row, status="connected", records_synced=records)
            return SyncResult(
                provider=self.provider,
                success=True,
                status=IntegrationConnectionStatus.connected,
                records_synced=records,
                message=f"Synced {records} GA4 daily rows.",
            )
        except Exception as exc:
            await mark_sync(db, row, status="sync_error", error=str(exc))
            return SyncResult(
                provider=self.provider,
                success=False,
                status=IntegrationConnectionStatus.sync_error,
                message="Google Analytics sync failed.",
                errors=[str(exc)],
            )

    async def _sync_ga4(
        self,
        db: AsyncSession,
        organization_id: UUID,
        client_id: UUID | None,
        access_token: str,
        property_id: str,
    ) -> int:
        end = date.today()
        start = end - timedelta(days=6)
        body = {
            "dateRanges": [{"startDate": start.isoformat(), "endDate": end.isoformat()}],
            "dimensions": [{"name": "date"}],
            "metrics": [
                {"name": "sessions"},
                {"name": "conversions"},
                {"name": "totalRevenue"},
                {"name": "screenPageViews"},
            ],
        }
        async with httpx.AsyncClient(timeout=45) as client:
            resp = await client.post(
                f"{GA_DATA}/{property_id}:runReport",
                headers={"Authorization": f"Bearer {access_token}"},
                json=body,
            )
            if resp.status_code >= 400:
                raise RuntimeError(resp.text)
            rows = resp.json().get("rows") or []
            if not client_id:
                return len(rows)
            count = 0
            for row in rows:
                dims = row.get("dimensionValues") or []
                mets = row.get("metricValues") or []
                if not dims:
                    continue
                day = datetime.strptime(dims[0]["value"], "%Y%m%d").date()
                sessions = int(float(mets[0]["value"])) if len(mets) > 0 else 0
                conversions = int(float(mets[1]["value"])) if len(mets) > 1 else 0
                revenue = Decimal(str(mets[2]["value"])) if len(mets) > 2 else Decimal("0")
                views = int(float(mets[3]["value"])) if len(mets) > 3 else 0
                db.add(
                    AnalyticsDaily(
                        organization_id=organization_id,
                        client_id=client_id,
                        date=day,
                        spend=Decimal("0"),
                        leads=conversions,
                        revenue=revenue,
                        impressions=views,
                        clicks=sessions,
                        conversions=conversions,
                        metrics={"source": "ga4_live", "provider": "google_analytics"},
                        data_source=DataSource.live,
                    )
                )
                count += 1
            await db.flush()
            return count
