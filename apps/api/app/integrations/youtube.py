"""YouTube OAuth + channel metrics sync (Phase 4)."""

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
from app.models.marketing import AnalyticsDaily, SocialAccount

YT_API = "https://www.googleapis.com/youtube/v3"
YT_SCOPE = "https://www.googleapis.com/auth/youtube.readonly"


class YouTubeIntegration(MarketingIntegration):
    provider = "youtube"
    display_name = "YouTube"

    def credentials_configured(self) -> bool:
        settings = get_settings()
        return bool(settings.google_client_id and settings.google_client_secret)

    def _redirect_uri(self) -> str:
        settings = get_settings()
        return (
            settings.youtube_redirect_uri
            or f"{settings.api_public_url}/api/v1/integrations/youtube/callback"
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
                message="YouTube connected. Tokens stored encrypted server-side.",
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
                message="Demo content available. Live YouTube is not connected.",
                credentials_configured=configured,
                can_connect=configured,
            )
        return ConnectionStatus(
            provider=self.provider,
            status=IntegrationConnectionStatus.not_connected,
            message=(
                "Not connected. Configure GOOGLE_CLIENT_ID/SECRET and complete OAuth."
                if not configured
                else "Ready to connect YouTube."
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
            "scope": " ".join([YT_SCOPE, "openid", "email"]),
            "access_type": "offline",
            "prompt": "consent",
            "state": state,
        }
        return ConnectResult(
            provider=self.provider,
            authorize_url=f"{GOOGLE_AUTH}?{urlencode(params)}",
            message="Redirect the user to Google to authorize YouTube access.",
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

        channel_id, account_label = await self._resolve_channel(access_token)

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
                "channel_id": channel_id,
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
        if client_id and channel_id:
            await self._upsert_social_account(
                db, organization_id=org_id, client_id=client_id, channel_id=channel_id, name=account_label
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
                message="Live sync requires a connected YouTube channel.",
                errors=["not_connected"],
            )
        channel_id = (row.config or {}).get("channel_id")
        if not channel_id:
            await mark_sync(db, row, status="sync_error", error="No YouTube channel")
            return SyncResult(
                provider=self.provider,
                success=False,
                status=IntegrationConnectionStatus.sync_error,
                message="No YouTube channel available on the connected account.",
                errors=["missing_channel"],
            )
        try:
            access_token = await ensure_access_token(
                db, row, organization_id=organization_id, provider=self.provider, client_id=client_id
            )
            records = await self._sync_channel(
                db, organization_id, client_id, access_token, str(channel_id)
            )
            await mark_sync(db, row, status="connected", records_synced=records)
            return SyncResult(
                provider=self.provider,
                success=True,
                status=IntegrationConnectionStatus.connected,
                records_synced=records,
                message=f"Synced {records} YouTube metric rows.",
            )
        except Exception as exc:
            await mark_sync(db, row, status="sync_error", error=str(exc))
            return SyncResult(
                provider=self.provider,
                success=False,
                status=IntegrationConnectionStatus.sync_error,
                message="YouTube sync failed.",
                errors=[str(exc)],
            )

    async def _resolve_channel(self, access_token: str) -> tuple[str | None, str]:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(
                f"{YT_API}/channels",
                params={"part": "snippet,statistics", "mine": "true"},
                headers={"Authorization": f"Bearer {access_token}"},
            )
        if resp.status_code >= 400:
            return None, "YouTube"
        items = resp.json().get("items") or []
        if not items:
            return None, "YouTube (no channel)"
        ch = items[0]
        channel_id = ch.get("id")
        title = (ch.get("snippet") or {}).get("title") or "YouTube"
        return channel_id, title

    async def _sync_channel(
        self,
        db: AsyncSession,
        organization_id: UUID,
        client_id: UUID | None,
        access_token: str,
        channel_id: str,
    ) -> int:
        async with httpx.AsyncClient(timeout=45) as client:
            ch_resp = await client.get(
                f"{YT_API}/channels",
                params={"part": "snippet,statistics", "id": channel_id},
                headers={"Authorization": f"Bearer {access_token}"},
            )
            if ch_resp.status_code >= 400:
                raise RuntimeError(ch_resp.text)
            channels = ch_resp.json().get("items") or []
            if not channels:
                return 0

            search_resp = await client.get(
                f"{YT_API}/search",
                params={
                    "part": "id",
                    "channelId": channel_id,
                    "order": "date",
                    "maxResults": 10,
                    "type": "video",
                },
                headers={"Authorization": f"Bearer {access_token}"},
            )
            video_ids: list[str] = []
            if search_resp.status_code < 400:
                for item in search_resp.json().get("items") or []:
                    vid = ((item.get("id") or {}).get("videoId"))
                    if vid:
                        video_ids.append(vid)

            views_recent = 0
            likes_recent = 0
            if video_ids:
                vids_resp = await client.get(
                    f"{YT_API}/videos",
                    params={"part": "statistics", "id": ",".join(video_ids)},
                    headers={"Authorization": f"Bearer {access_token}"},
                )
                if vids_resp.status_code < 400:
                    for v in vids_resp.json().get("items") or []:
                        stats = v.get("statistics") or {}
                        views_recent += int(stats.get("viewCount") or 0)
                        likes_recent += int(stats.get("likeCount") or 0)

        if not client_id:
            return 1

        ch = channels[0]
        stats = ch.get("statistics") or {}
        title = (ch.get("snippet") or {}).get("title") or "YouTube"
        await self._upsert_social_account(
            db,
            organization_id=organization_id,
            client_id=client_id,
            channel_id=channel_id,
            name=title,
            meta={
                "subscriber_count": int(stats.get("subscriberCount") or 0),
                "video_count": int(stats.get("videoCount") or 0),
                "view_count": int(stats.get("viewCount") or 0),
                "recent_video_views": views_recent,
                "recent_video_likes": likes_recent,
                "source": "youtube_live",
            },
        )

        # Channel totals are lifetime — store a single live snapshot day, never invent history.
        db.add(
            AnalyticsDaily(
                organization_id=organization_id,
                client_id=client_id,
                date=date.today(),
                spend=Decimal("0"),
                leads=0,
                revenue=Decimal("0"),
                impressions=views_recent or int(stats.get("viewCount") or 0),
                clicks=likes_recent,
                conversions=0,
                metrics={
                    "source": "youtube_live",
                    "provider": "youtube",
                    "channel_id": channel_id,
                    "subscriber_count": int(stats.get("subscriberCount") or 0),
                    "video_count": int(stats.get("videoCount") or 0),
                    "lifetime_views": int(stats.get("viewCount") or 0),
                    "recent_video_views": views_recent,
                    "note": "Snapshot from YouTube Data API; not fabricated daily history.",
                },
                data_source=DataSource.live,
            )
        )
        await db.flush()
        return 1 + (1 if video_ids else 0)

    async def _upsert_social_account(
        self,
        db: AsyncSession,
        *,
        organization_id: UUID,
        client_id: UUID,
        channel_id: str,
        name: str,
        meta: dict | None = None,
    ) -> SocialAccount:
        row = await db.scalar(
            select(SocialAccount).where(
                SocialAccount.organization_id == organization_id,
                SocialAccount.client_id == client_id,
                SocialAccount.provider == "youtube",
                SocialAccount.external_id == channel_id,
            ).limit(1)
        )
        if row:
            row.name = name
            row.connection_status = ModelConnectionStatus.connected
            row.last_synced_at = datetime.now(timezone.utc)
            if meta is not None:
                row.meta = meta
            return row
        row = SocialAccount(
            organization_id=organization_id,
            client_id=client_id,
            provider="youtube",
            external_id=channel_id,
            name=name,
            connection_status=ModelConnectionStatus.connected,
            encrypted_credentials_ref=None,
            meta=meta or {"source": "youtube_live"},
            last_synced_at=datetime.now(timezone.utc),
        )
        db.add(row)
        await db.flush()
        return row
