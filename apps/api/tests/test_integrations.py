import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app


@pytest.mark.asyncio
async def test_integration_statuses_and_connect_without_credentials():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        login = await client.post("/api/v1/auth/login", json={"email": "demo@growthos.ai", "password": "demo1234"})
        assert login.status_code == 200
        headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

        statuses = await client.get("/api/v1/integrations", headers=headers)
        assert statuses.status_code == 200
        body = statuses.json()
        providers = {item["provider"]: item for item in body}
        assert "meta" in providers
        assert "instagram" in providers
        assert "whatsapp" in providers
        assert "google_analytics" in providers
        assert "google_ads" in providers
        assert "youtube" in providers
        # Never fake Connected without OAuth
        assert providers["meta"]["status"] in {"demo_data", "not_connected"}
        assert providers["meta"]["status"] != "connected"
        assert providers["google_ads"]["status"] in {"demo_data", "not_connected"}
        assert providers["google_ads"]["status"] != "connected"
        assert providers["youtube"]["status"] in {"demo_data", "not_connected"}
        assert providers["youtube"]["can_connect"] is False or providers["youtube"]["credentials_configured"] is False

        connect = await client.post("/api/v1/integrations/meta/connect", headers=headers)
        assert connect.status_code == 400
        assert "not configured" in connect.json()["detail"].lower()

        ads_connect = await client.post("/api/v1/integrations/google_ads/connect", headers=headers)
        assert ads_connect.status_code == 400
        assert "not configured" in ads_connect.json()["detail"].lower()

        yt_connect = await client.post("/api/v1/integrations/youtube/connect", headers=headers)
        assert yt_connect.status_code == 400
        assert "not configured" in yt_connect.json()["detail"].lower()

        sync = await client.post("/api/v1/integrations/meta/sync", headers=headers)
        assert sync.status_code == 200
        assert sync.json()["success"] is False
        assert sync.json()["status"] in {"demo_data", "not_connected"}

        ads_sync = await client.post("/api/v1/integrations/google_ads/sync", headers=headers)
        assert ads_sync.status_code == 200
        assert ads_sync.json()["success"] is False


@pytest.mark.asyncio
async def test_campaigns_list_includes_demo_seed():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        login = await client.post("/api/v1/auth/login", json={"email": "demo@growthos.ai", "password": "demo1234"})
        assert login.status_code == 200
        headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

        camps = await client.get("/api/v1/campaigns", headers=headers)
        assert camps.status_code == 200
        body = camps.json()
        assert isinstance(body, list)
        assert len(body) >= 1
        assert all("data_source" in c for c in body)
        assert all(c["data_source"] in {"demo", "live"} for c in body)
