"""Meta Graph OAuth shared by Meta Ads, Instagram, and WhatsApp Cloud API."""

from __future__ import annotations

from datetime import date, datetime, timezone
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
from app.models.enums import ConnectionStatus as AccountConnectionStatus
from app.models.enums import DataSource
from app.models.marketing import AnalyticsDaily, SocialAccount


META_AUTH_URL = "https://www.facebook.com/v21.0/dialog/oauth"
META_TOKEN_URL = "https://graph.facebook.com/v21.0/oauth/access_token"
META_GRAPH = "https://graph.facebook.com/v21.0"


PROVIDER_SCOPES = {
    "meta": ["ads_read", "ads_management", "business_management", "read_insights"],
    "instagram": ["instagram_basic", "instagram_manage_insights", "pages_show_list", "pages_read_engagement"],
    "whatsapp": ["whatsapp_business_management", "whatsapp_business_messaging", "business_management"],
}


class MetaFamilyIntegration(MarketingIntegration):
    def __init__(self, provider: str, display_name: str) -> None:
        self.provider = provider
        self.display_name = display_name

    def credentials_configured(self) -> bool:
        settings = get_settings()
        return bool(settings.meta_app_id and settings.meta_app_secret)

    def _redirect_uri(self) -> str:
        settings = get_settings()
        return settings.meta_redirect_uri or f"{settings.api_public_url}/api/v1/integrations/{self.provider}/callback"

    async def get_connection_status(self, organization_id: UUID, client_id: UUID | None = None) -> ConnectionStatus:
        # Persistence is injected by IntegrationService via set_db before calls.
        db: AsyncSession = self._db  # type: ignore[attr-defined]
        row = await get_integration_row(db, organization_id=organization_id, provider=self.provider, client_id=client_id)
        configured = self.credentials_configured()
        if row and row.status == "connected" and row.secret_ref:
            cfg = row.config or {}
            return ConnectionStatus(
                provider=self.provider,
                status=IntegrationConnectionStatus.connected,
                message=f"{self.display_name} connected. Tokens stored encrypted server-side.",
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
                can_connect=configured and not bool(row.secret_ref),
            )
        settings = get_settings()
        if settings.demo_mode and (not row or row.status in {"demo_data", "not_connected", None}):
            # Only claim demo_data when not actually connected — never fake Connected.
            if not row or not row.secret_ref:
                return ConnectionStatus(
                    provider=self.provider,
                    status=IntegrationConnectionStatus.demo_data,
                    message="Demo analytics available. Live Meta credentials are not connected.",
                    credentials_configured=configured,
                    can_connect=configured,
                )
        return ConnectionStatus(
            provider=self.provider,
            status=IntegrationConnectionStatus.not_connected,
            message="Not connected. Configure META_APP_ID/SECRET and complete OAuth to connect."
            if not configured
            else f"Ready to connect {self.display_name} via Meta OAuth.",
            credentials_configured=configured,
            can_connect=configured,
        )

    async def build_authorize_url(
        self, *, organization_id: UUID, user_id: UUID, client_id: UUID | None
    ) -> ConnectResult:
        if not self.credentials_configured():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Meta app credentials not configured. Set META_APP_ID and META_APP_SECRET.",
            )
        settings = get_settings()
        state = encode_oauth_state(
            provider=self.provider,
            organization_id=organization_id,
            client_id=client_id,
            user_id=user_id,
        )
        params = {
            "client_id": settings.meta_app_id,
            "redirect_uri": self._redirect_uri(),
            "state": state,
            "response_type": "code",
            "scope": ",".join(PROVIDER_SCOPES[self.provider]),
        }
        return ConnectResult(
            provider=self.provider,
            authorize_url=f"{META_AUTH_URL}?{urlencode(params)}",
            message="Redirect the user to Meta to authorize access. No tokens are exposed to the browser.",
        )

    async def handle_callback(self, *, code: str, state: str) -> dict:
        db: AsyncSession = self._db  # type: ignore[attr-defined]
        settings = get_settings()
        try:
            payload = decode_oauth_state(state)
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
        if payload.get("provider") != self.provider:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Provider mismatch in OAuth state")

        async with httpx.AsyncClient(timeout=30) as client:
            token_resp = await client.get(
                META_TOKEN_URL,
                params={
                    "client_id": settings.meta_app_id,
                    "client_secret": settings.meta_app_secret,
                    "redirect_uri": self._redirect_uri(),
                    "code": code,
                },
            )
            if token_resp.status_code >= 400:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Meta token exchange failed: {token_resp.text}",
                )
            token_data = token_resp.json()
            access_token = token_data.get("access_token")
            if not access_token:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Meta did not return an access token")

            me_resp = await client.get(f"{META_GRAPH}/me", params={"access_token": access_token, "fields": "id,name"})
            me = me_resp.json() if me_resp.status_code < 400 else {}

        org_id = UUID(payload["organization_id"])
        client_id = UUID(payload["client_id"]) if payload.get("client_id") else None
        await upsert_integration(
            db,
            organization_id=org_id,
            provider=self.provider,
            client_id=client_id,
            status="connected",
            config={
                "account_label": me.get("name") or me.get("id") or self.display_name,
                "external_account_id": me.get("id"),
                "connected_at": datetime.now(timezone.utc).isoformat(),
                "token_type": "meta_user",
            },
            token_payload={
                "access_token": access_token,
                "token_type": token_data.get("token_type", "bearer"),
                "expires_in": token_data.get("expires_in"),
                "provider": self.provider,
            },
        )
        if client_id:
            db.add(
                SocialAccount(
                    organization_id=org_id,
                    client_id=client_id,
                    provider=self.provider,
                    external_id=me.get("id"),
                    name=me.get("name") or self.display_name,
                    connection_status=AccountConnectionStatus.connected,
                    encrypted_credentials_ref=None,  # tokens live on Integration.secret_ref
                    meta={"phase": 3},
                    last_synced_at=None,
                )
            )
            await db.flush()
        return {
            "provider": self.provider,
            "organization_id": str(org_id),
            "client_id": str(client_id) if client_id else None,
            "account_label": me.get("name"),
        }

    async def disconnect(self, organization_id: UUID, client_id: UUID | None = None) -> ConnectionStatus:
        db: AsyncSession = self._db  # type: ignore[attr-defined]
        row = await get_integration_row(db, organization_id=organization_id, provider=self.provider, client_id=client_id)
        if row:
            await clear_integration_secrets(db, row)
        return await self.get_connection_status(organization_id, client_id)

    async def sync(self, organization_id: UUID, client_id: UUID | None = None) -> SyncResult:
        db: AsyncSession = self._db  # type: ignore[attr-defined]
        row = await get_integration_row(db, organization_id=organization_id, provider=self.provider, client_id=client_id)
        if not row or not row.secret_ref or row.status not in {"connected", "sync_error"}:
            status_now = await self.get_connection_status(organization_id, client_id)
            return SyncResult(
                provider=self.provider,
                success=False,
                status=status_now.status,
                message="Live sync requires a connected account. Demo data is not treated as a live connection.",
                errors=["not_connected"],
            )

        tokens = load_tokens(row)
        if not tokens or not tokens.get("access_token"):
            await mark_sync(db, row, status="sync_error", error="Encrypted token missing or unreadable")
            return SyncResult(
                provider=self.provider,
                success=False,
                status=IntegrationConnectionStatus.sync_error,
                message="Stored credentials could not be decrypted.",
                errors=["token_unavailable"],
            )

        try:
            records = await self._sync_live(db, organization_id, client_id, tokens["access_token"])
            await mark_sync(db, row, status="connected", records_synced=records)
            return SyncResult(
                provider=self.provider,
                success=True,
                status=IntegrationConnectionStatus.connected,
                records_synced=records,
                message=f"Synced {records} live records from {self.display_name}.",
            )
        except Exception as exc:
            await mark_sync(db, row, status="sync_error", error=str(exc))
            return SyncResult(
                provider=self.provider,
                success=False,
                status=IntegrationConnectionStatus.sync_error,
                message="Sync failed against Meta Graph API.",
                errors=[str(exc)],
            )

    async def _sync_live(
        self, db: AsyncSession, organization_id: UUID, client_id: UUID | None, access_token: str
    ) -> int:
        """Pull available insights. Never invent metrics if the API returns nothing."""
        records = 0
        async with httpx.AsyncClient(timeout=45) as client:
            if self.provider == "meta":
                resp = await client.get(
                    f"{META_GRAPH}/me/adaccounts",
                    params={"access_token": access_token, "fields": "id,name,account_id"},
                )
                if resp.status_code >= 400:
                    raise RuntimeError(f"adaccounts error: {resp.text}")
                accounts = resp.json().get("data") or []
                if not accounts:
                    return 0
                account_id = accounts[0]["id"]
                insights = await client.get(
                    f"{META_GRAPH}/{account_id}/insights",
                    params={
                        "access_token": access_token,
                        "date_preset": "last_7d",
                        "time_increment": 1,
                        "fields": "spend,impressions,clicks,actions,date_start",
                    },
                )
                if insights.status_code >= 400:
                    raise RuntimeError(f"insights error: {insights.text}")
                if not client_id:
                    return len(insights.json().get("data") or [])
                for row in insights.json().get("data") or []:
                    day = date.fromisoformat(row["date_start"])
                    spend = Decimal(str(row.get("spend") or 0))
                    impressions = int(row.get("impressions") or 0)
                    clicks = int(row.get("clicks") or 0)
                    actions = row.get("actions") or []
                    leads = 0
                    for action in actions:
                        if action.get("action_type") in {"lead", "onsite_conversion.messaging_conversation_started_7d"}:
                            leads += int(action.get("value") or 0)
                    db.add(
                        AnalyticsDaily(
                            organization_id=organization_id,
                            client_id=client_id,
                            date=day,
                            spend=spend,
                            leads=leads,
                            revenue=Decimal("0"),
                            impressions=impressions,
                            clicks=clicks,
                            conversions=leads,
                            metrics={"source": "meta_live", "provider": "meta"},
                            data_source=DataSource.live,
                        )
                    )
                    records += 1
                await db.flush()
                return records

            # Instagram / WhatsApp: verify token + list reachable objects; persist sync metadata only
            me = await client.get(f"{META_GRAPH}/me", params={"access_token": access_token, "fields": "id,name"})
            if me.status_code >= 400:
                raise RuntimeError(me.text)
            # Without additional asset IDs we do not invent daily metrics.
            return 1 if me.json().get("id") else 0


class MetaIntegration(MetaFamilyIntegration):
    def __init__(self) -> None:
        super().__init__("meta", "Meta Ads")


class InstagramIntegration(MetaFamilyIntegration):
    def __init__(self) -> None:
        super().__init__("instagram", "Instagram")


class WhatsAppIntegration(MetaFamilyIntegration):
    def __init__(self) -> None:
        super().__init__("whatsapp", "WhatsApp")
