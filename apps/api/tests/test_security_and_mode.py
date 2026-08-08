import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app


@pytest.mark.asyncio
async def test_tenant_isolation_on_actions_and_clients():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        login = await client.post("/api/v1/auth/login", json={"email": "demo@growthos.ai", "password": "demo1234"})
        headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

        # Foreign client id must 404 on action create
        bad = await client.post(
            "/api/v1/autopilot/actions",
            headers=headers,
            json={
                "action_type": "CREATE_CONTENT",
                "client_id": "00000000-0000-0000-0000-000000000099",
                "platform": "instagram",
                "description": "x",
                "reason": "tenant test",
                "agent": "ContentAgent",
            },
        )
        assert bad.status_code == 404

        # Financial without cost blocked
        clients = await client.get("/api/v1/clients", headers=headers)
        client_id = clients.json()[0]["id"]
        nocost = await client.post(
            "/api/v1/autopilot/actions",
            headers=headers,
            json={
                "action_type": "CREATE_CAMPAIGN",
                "client_id": client_id,
                "platform": "meta",
                "description": "No cost",
                "reason": "budget required",
                "agent": "AdsAgent",
            },
        )
        assert nocost.status_code == 400
        assert "BUDGET_REQUIRED" in nocost.json()["detail"]


@pytest.mark.asyncio
async def test_operating_mode_and_dashboard_data_source():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        login = await client.post("/api/v1/auth/login", json={"email": "demo@growthos.ai", "password": "demo1234"})
        headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

        me = await client.get("/api/v1/auth/me", headers=headers)
        assert me.status_code == 200
        body = me.json()
        assert body["operating_mode"] in {"DEMO", "LIVE"}
        assert "organization_demo_mode" in body

        dash = await client.get("/api/v1/dashboard", headers=headers)
        assert dash.status_code == 200
        kpis = dash.json()["kpis"]
        assert kpis["data_source"] in {"demo", "live", "mixed"}
        # Health must be null or derived — never a magic constant-only path without metrics
        if kpis["total_ad_spend"] in (0, "0", "0.00") and kpis["total_leads"] == 0:
            assert kpis["marketing_health_score"] is None
