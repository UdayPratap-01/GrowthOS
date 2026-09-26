"""Google Search Console OAuth + read-only search analytics (development-safe)."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from urllib.parse import quote, urlencode
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
from app.integrations.google_oauth import GOOGLE_AUTH, ensure_access_token, exchange_code
from app.integrations.oauth import decode_oauth_state, encode_oauth_state
from app.integrations.persistence import (
    clear_integration_secrets,
    get_integration_row,
    mark_sync,
    upsert_integration,
)

GSC_API = "https://www.googleapis.com/webmasters/v3"
GSC_SCOPE = "https://www.googleapis.com/auth/webmasters.readonly"


class GoogleSearchConsoleIntegration(MarketingIntegration):
    provider = "google_search_console"
    display_name = "Google Search Console"

    def credentials_configured(self) -> bool:
        settings = get_settings()
        return bool(settings.google_client_id and settings.google_client_secret)

    def _redirect_uri(self) -> str:
        settings = get_settings()
        return (
            settings.google_search_console_redirect_uri
            or f"{settings.api_public_url}/api/v1/integrations/google_search_console/callback"
        )

    async def get_connection_status(
        self, organization_id: UUID, client_id: UUID | None = None
    ) -> ConnectionStatus:
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
                message="Search Console connected. Read-only site and query data available.",
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
                message="Demo mode. Connect Search Console via Google OAuth for live SEO data.",
                credentials_configured=configured,
                can_connect=configured,
            )
        return ConnectionStatus(
            provider=self.provider,
            status=IntegrationConnectionStatus.not_connected,
            message=(
                "Not connected. Configure GOOGLE_CLIENT_ID/SECRET and complete OAuth."
                if not configured
                else "Ready to connect Google Search Console (read-only)."
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
            "scope": " ".join([GSC_SCOPE, "openid", "email"]),
            "access_type": "offline",
            "prompt": "consent",
            "state": state,
        }
        return ConnectResult(
            provider=self.provider,
            authorize_url=f"{GOOGLE_AUTH}?{urlencode(params)}",
            message="Redirect the user to Google to authorize Search Console read access.",
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
        sites = await self._list_sites(access_token)
        primary = sites[0] if sites else {}
        site_url = primary.get("siteUrl") or ""
        label = site_url or "Search Console"

        org_id = UUID(payload["organization_id"])
        client_id = UUID(payload["client_id"]) if payload.get("client_id") else None
        await upsert_integration(
            db,
            organization_id=org_id,
            provider=self.provider,
            client_id=client_id,
            status="connected",
            config={
                "account_label": label,
                "site_url": site_url,
                "sites": sites[:25],
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
            "account_label": label,
            "site_count": len(sites),
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
                message="Live sync requires a connected Search Console property.",
                errors=["not_connected"],
            )
        site_url = (row.config or {}).get("site_url")
        if not site_url:
            await mark_sync(db, row, status="sync_error", error="No Search Console site selected")
            return SyncResult(
                provider=self.provider,
                success=False,
                status=IntegrationConnectionStatus.sync_error,
                message="No verified Search Console property on the connected account.",
                errors=["missing_site"],
            )
        try:
            access_token = await ensure_access_token(
                db, row, organization_id=organization_id, provider=self.provider, client_id=client_id
            )
            sites = await self._list_sites(access_token)
            analytics = await self._fetch_search_analytics(access_token, site_url)
            cfg = dict(row.config or {})
            cfg["sites"] = sites[:25]
            cfg["last_search_analytics"] = analytics
            cfg["analytics_synced_at"] = datetime.now(timezone.utc).isoformat()
            row.config = cfg
            await db.flush()
            await mark_sync(db, row, status="connected", records_synced=len(analytics.get("rows") or []))
            return SyncResult(
                provider=self.provider,
                success=True,
                status=IntegrationConnectionStatus.connected,
                records_synced=len(analytics.get("rows") or []),
                message="Synced Search Console query/page analytics (read-only).",
            )
        except Exception as exc:
            await mark_sync(db, row, status="sync_error", error=str(exc)[:300])
            return SyncResult(
                provider=self.provider,
                success=False,
                status=IntegrationConnectionStatus.sync_error,
                message="Search Console sync failed.",
                errors=[type(exc).__name__],
            )

    async def _list_sites(self, access_token: str) -> list[dict]:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(
                f"{GSC_API}/sites",
                headers={"Authorization": f"Bearer {access_token}"},
            )
        if resp.status_code >= 400:
            raise RuntimeError(f"Search Console sites list failed: HTTP {resp.status_code}")
        return [
            {"siteUrl": entry.get("siteUrl"), "permissionLevel": entry.get("permissionLevel")}
            for entry in (resp.json().get("siteEntry") or [])
            if entry.get("siteUrl")
        ]

    async def _fetch_search_analytics(self, access_token: str, site_url: str) -> dict:
        end = date.today()
        start = end - timedelta(days=7)
        body = {
            "startDate": start.isoformat(),
            "endDate": end.isoformat(),
            "dimensions": ["query", "page"],
            "rowLimit": 25,
        }
        encoded_site = quote(site_url, safe="")
        async with httpx.AsyncClient(timeout=45) as client:
            resp = await client.post(
                f"{GSC_API}/sites/{encoded_site}/searchAnalytics/query",
                headers={"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"},
                json=body,
            )
        if resp.status_code >= 400:
            raise RuntimeError(f"Search analytics query failed: HTTP {resp.status_code}")
        data = resp.json()
        rows = []
        for row in data.get("rows") or []:
            keys = row.get("keys") or []
            rows.append(
                {
                    "query": keys[0] if len(keys) > 0 else None,
                    "page": keys[1] if len(keys) > 1 else None,
                    "clicks": row.get("clicks"),
                    "impressions": row.get("impressions"),
                    "ctr": row.get("ctr"),
                    "position": row.get("position"),
                }
            )
        return {
            "start_date": start.isoformat(),
            "end_date": end.isoformat(),
            "rows": rows,
            "source": "search_console_api",
            "note": "Read-only Search Console data; not fabricated.",
        }
