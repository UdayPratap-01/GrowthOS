"""YouTube integration — read-only capabilities and cached reads."""

from __future__ import annotations

import json
import uuid

import pytest
from httpx import ASGITransport, AsyncClient

from app.core.security import hash_password
from app.db.session import AsyncSessionLocal
from app.integrations.persistence import upsert_integration
from app.main import app
from app.models.enums import MemberRole
from app.models.organization import Organization, OrganizationMember
from app.models.user import User
from app.publishing.capabilities import CapabilityStatus, youtube_capabilities


def test_youtube_capabilities_publish_disabled():
    matrix = youtube_capabilities(connected=True, credentials_configured=True)
    by_op = {c.operation: c for c in matrix.capabilities}
    assert by_op["get_channel"].status == CapabilityStatus.supported
    assert by_op["publish_video"].status == CapabilityStatus.unsupported


@pytest.mark.asyncio
async def test_youtube_videos_read_endpoint():
    email = f"yt-{uuid.uuid4().hex[:6]}@t.com"
    async with AsyncSessionLocal() as db:
        org = Organization(name="YT", slug=f"yt-{uuid.uuid4().hex[:6]}", demo_mode=False)
        user = User(email=email, hashed_password=hash_password("pass"), full_name="YT")
        db.add_all([org, user])
        await db.flush()
        db.add(OrganizationMember(organization_id=org.id, user_id=user.id, role=MemberRole.owner))
        await upsert_integration(
            db,
            organization_id=org.id,
            provider="youtube",
            client_id=None,
            status="connected",
            config={
                "channel_id": "UC123",
                "account_label": "Demo Channel",
                "channel_stats": {"subscriber_count": 100, "video_count": 5, "view_count": 999},
                "recent_videos": [
                    {
                        "id": "vid1",
                        "title": "Hello",
                        "view_count": 10,
                        "like_count": 2,
                        "comment_count": 1,
                    }
                ],
            },
            token_payload={"access_token": "yt-token", "provider": "youtube"},
        )
        await db.commit()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        login = await client.post("/api/v1/auth/login", json={"email": email, "password": "pass"})
        headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
        caps = await client.get("/api/v1/seo/integrations/youtube/capabilities", headers=headers)
        assert caps.status_code == 200
        assert caps.json()["provider"] == "youtube"

        videos = await client.get("/api/v1/seo/integrations/youtube/videos", headers=headers)
        assert videos.status_code == 200
        body = videos.json()
        assert body["channel_id"] == "UC123"
        assert body["videos"][0]["title"] == "Hello"
        assert body["read_only"] is True
        assert "access_token" not in json.dumps(body)
