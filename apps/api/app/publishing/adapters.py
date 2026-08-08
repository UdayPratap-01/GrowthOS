"""Platform publishers — honest about connection / capability."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.integrations.persistence import get_integration_row
from app.publishing.base import PublishResult, SocialPublisher


class BaseHonestPublisher(SocialPublisher):
    provider_key: str

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def _connected(self, organization_id: UUID, client_id: UUID | None) -> bool:
        row = await get_integration_row(
            self.db, organization_id=organization_id, provider=self.provider_key, client_id=client_id
        )
        if row and row.status == "connected" and row.secret_ref:
            return True
        if client_id is not None:
            org_row = await get_integration_row(
                self.db, organization_id=organization_id, provider=self.provider_key, client_id=None
            )
            return bool(org_row and org_row.status == "connected" and org_row.secret_ref)
        return False

    def credentials_ready(self, *, organization_id, client_id) -> bool:
        # Sync helper for UI; real check is async in publish.
        return False

    async def publish(self, *, content: dict, organization_id, client_id) -> PublishResult:
        settings = get_settings()
        connected = await self._connected(organization_id, client_id)
        if not connected:
            return PublishResult(
                success=False,
                status="not_connected",
                message=f"{self.platform.upper()} NOT CONNECTED",
                error=f"{self.platform.upper()} NOT CONNECTED",
            )
        if settings.demo_mode:
            return PublishResult(
                success=True,
                status="demo_simulated",
                message="DEMO DATA — publish simulated; no live platform post created.",
                external_id=None,
                platform_response={"note": "DEMO DATA"},
                demo=True,
            )
        # Live write adapters require platform-specific publish scopes not configured in Phase 5 base.
        return PublishResult(
            success=False,
            status="not_implemented",
            message=f"{self.platform} live publish requires additional platform publish permissions.",
            error="PUBLISH_NOT_AVAILABLE",
        )

    async def schedule(self, *, content: dict, scheduled_for: str, organization_id, client_id) -> PublishResult:
        result = await self.publish(content=content, organization_id=organization_id, client_id=client_id)
        if result.demo:
            result.message = "DEMO DATA — schedule simulated; no live platform schedule created."
            result.status = "demo_scheduled"
        return result

    async def get_status(self, *, external_id: str, organization_id, client_id) -> PublishResult:
        if not external_id:
            return PublishResult(success=False, status="missing_id", message="INSUFFICIENT DATA", error="INSUFFICIENT DATA")
        return PublishResult(
            success=False,
            status="unknown",
            message="STATUS LOOKUP NOT AVAILABLE for this adapter yet.",
            error="STATUS_NOT_AVAILABLE",
        )

    async def delete(self, *, external_id: str, organization_id, client_id) -> PublishResult:
        return PublishResult(
            success=False,
            status="not_available",
            message="ROLLBACK NOT AVAILABLE",
            error="ROLLBACK NOT AVAILABLE",
        )


class MetaPublisher(BaseHonestPublisher):
    platform = "meta"
    provider_key = "meta"


class InstagramPublisher(BaseHonestPublisher):
    platform = "instagram"
    provider_key = "instagram"


class WhatsAppPublisher(BaseHonestPublisher):
    platform = "whatsapp"
    provider_key = "whatsapp"


class YouTubePublisher(BaseHonestPublisher):
    platform = "youtube"
    provider_key = "youtube"


class LinkedInPublisher(BaseHonestPublisher):
    platform = "linkedin"
    provider_key = "linkedin"

    async def publish(self, *, content: dict, organization_id, client_id) -> PublishResult:
        return PublishResult(
            success=False,
            status="not_connected",
            message="LINKEDIN NOT CONNECTED",
            error="INTEGRATION NOT CONNECTED",
        )


def get_publisher(db: AsyncSession, platform: str) -> SocialPublisher | None:
    mapping = {
        "meta": MetaPublisher,
        "facebook": MetaPublisher,
        "instagram": InstagramPublisher,
        "whatsapp": WhatsAppPublisher,
        "youtube": YouTubePublisher,
        "linkedin": LinkedInPublisher,
    }
    cls = mapping.get(platform.lower())
    return cls(db) if cls else None
