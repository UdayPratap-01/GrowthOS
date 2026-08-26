import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

# Force demo image provider for contract tests (real PNG file, labeled DEMO)
os.environ["DEMO_MODE"] = "true"
os.environ["IMAGE_PROVIDER"] = "demo"
os.environ["VIDEO_PROVIDER"] = "none"
os.environ["STORAGE_BACKEND"] = "local"
os.environ["STORAGE_LOCAL_PATH"] = "./storage_test_media"

from app.core.config import get_settings

get_settings.cache_clear()

from app.db.session import AsyncSessionLocal  # noqa: E402
from app.main import app  # noqa: E402
from app.generation.media_utils import is_valid_image, make_demo_png  # noqa: E402
from app.models.billing import SubscriptionStatus  # noqa: E402
from app.models.organization import OrganizationMember  # noqa: E402
from app.models.user import User  # noqa: E402
from app.services.billing_service import BillingService  # noqa: E402
from app.storage.object_storage import LocalObjectStorage  # noqa: E402


async def _ensure_demo_org_subscription_usable() -> None:
    """
    The media contract logs in as the long-lived seeded demo user. Billing's
    OrganizationSubscription for that org is created lazily with a 14-day trial
    and then ages in the shared SQLite DB — after trial_ends_at, quota-gated
    routes return 402. Refresh it here so this file does not depend on when the
    demo org was first billed.
    """
    async with AsyncSessionLocal() as db:
        user = await db.scalar(select(User).where(User.email == "demo@growthos.ai"))
        assert user is not None, "demo user must be seeded before this suite"
        membership = await db.scalar(
            select(OrganizationMember).where(OrganizationMember.user_id == user.id)
        )
        assert membership is not None
        service = BillingService(db)
        subscription = await service.get_subscription(membership.organization_id)
        subscription.trial_ends_at = datetime.now(timezone.utc) + timedelta(days=14)
        if subscription.status not in {
            SubscriptionStatus.TRIALING,
            SubscriptionStatus.ACTIVE,
            SubscriptionStatus.PAST_DUE,
        }:
            await service.set_status(
                membership.organization_id,
                SubscriptionStatus.ACTIVE,
                reason="Test fixture: restore usable demo subscription.",
            )
        await db.commit()


def test_demo_png_is_valid_image():
    data = make_demo_png(128, 128, "DEMO")
    assert is_valid_image(data)
    assert data.startswith(b"\x89PNG")


@pytest.mark.asyncio
async def test_local_storage_roundtrip():
    root = Path("./storage_test_media_unit")
    storage = LocalObjectStorage(root)
    key = "organizations/o1/clients/c1/campaigns/none/images/t.png"
    await storage.upload(make_demo_png(64, 64), key, "image/png")
    assert await storage.exists(key)
    raw = await storage.get_bytes(key)
    assert raw and is_valid_image(raw)
    await storage.delete(key)


@pytest.mark.asyncio
async def test_image_generation_demo_produces_file_and_media_endpoint():
    await _ensure_demo_org_subscription_usable()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test", timeout=60.0) as client:
        login = await client.post("/api/v1/auth/login", json={"email": "demo@growthos.ai", "password": "demo1234"})
        assert login.status_code == 200
        headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

        status = await client.get("/api/v1/creative/providers", headers=headers)
        assert status.status_code == 200
        body = status.json()
        assert body["image_configured"] is True
        assert body["video_configured"] is False

        clients = await client.get("/api/v1/clients", headers=headers)
        client_id = clients.json()[0]["id"]

        gen = await client.post(
            "/api/v1/creative/images/generate",
            headers=headers,
            json={
                "client_id": client_id,
                "prompt": "Test hero creative for unit test",
                "aspect_ratio": "1:1",
                "quantity": 1,
            },
        )
        assert gen.status_code == 200, gen.text
        job = gen.json()
        assert job["status"] == "COMPLETED"
        assert job["assets"], "COMPLETED requires stored assets"
        asset_id = job["assets"][0]["id"]
        assert job["assets"][0]["url"]

        media = await client.get(f"/api/v1/creative/media/{asset_id}", headers=headers)
        assert media.status_code == 200
        assert is_valid_image(media.content)
        assert media.headers.get("content-type", "").startswith("image/")

        # Tenant isolation: unauthenticated cannot fetch
        denied = await client.get(f"/api/v1/creative/media/{asset_id}")
        assert denied.status_code in {401, 403}

        # Video not configured — honest failure, no fake COMPLETED
        vid = await client.post(
            "/api/v1/creative/videos/generate",
            headers=headers,
            json={"client_id": client_id, "prompt": "short ad", "duration_seconds": 5},
        )
        assert vid.status_code == 200
        vbody = vid.json()
        assert vbody["status"] != "COMPLETED" or not vbody.get("assets")
        assert "NOT CONFIGURED" in (vbody.get("error") or vbody.get("message") or "")


@pytest.mark.asyncio
async def test_image_not_configured_when_provider_none(monkeypatch):
    monkeypatch.setenv("IMAGE_PROVIDER", "none")
    get_settings.cache_clear()
    from app.generation.image import get_image_provider

    assert get_image_provider().configured() is False
    result = await get_image_provider().generate_image(prompt="x")
    assert result.success is False
    assert "NOT CONFIGURED" in (result.error or "")
    get_settings.cache_clear()
    os.environ["IMAGE_PROVIDER"] = "demo"
