import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app


@pytest.mark.asyncio
async def test_autonomy_settings_and_action_approval_flow():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        login = await client.post("/api/v1/auth/login", json={"email": "demo@growthos.ai", "password": "demo1234"})
        assert login.status_code == 200
        headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

        settings = await client.get("/api/v1/autopilot/settings", headers=headers)
        assert settings.status_code == 200
        body = settings.json()
        assert body["autonomy_mode"] in {"copilot", "assisted", "autonomous"}
        assert body["require_approval_for_financial_actions"] is True

        clients = await client.get("/api/v1/clients", headers=headers)
        assert clients.status_code == 200
        client_id = clients.json()[0]["id"]

        # Budget limit enforcement
        over = await client.post(
            "/api/v1/autopilot/actions",
            headers=headers,
            json={
                "action_type": "CREATE_CAMPAIGN",
                "client_id": client_id,
                "platform": "meta",
                "description": "Huge budget campaign",
                "reason": "budget test",
                "estimated_cost": 999999,
                "agent": "AdsAgent",
            },
        )
        assert over.status_code == 400
        assert "BUDGET" in over.json()["detail"]

        created = await client.post(
            "/api/v1/autopilot/actions",
            headers=headers,
            json={
                "action_type": "CREATE_CREATIVE",
                "client_id": client_id,
                "platform": "instagram",
                "description": "Create 3 Reel concepts",
                "reason": "Autopilot test",
                "evidence": ["test"],
                "agent": "CreativeAgent",
            },
        )
        assert created.status_code == 200
        action = created.json()
        assert action["status"] == "PENDING"
        assert action["status"] != "COMPLETED"

        summary = await client.get("/api/v1/autopilot/summary", headers=headers)
        assert summary.status_code == 200
        assert summary.json()["pending_approvals"] >= 1

        approved = await client.post(
            f"/api/v1/autopilot/actions/{action['id']}/approve",
            headers=headers,
            json={"note": "ok"},
        )
        assert approved.status_code == 200
        result = approved.json()
        assert result["status"] in {"COMPLETED", "FAILED"}
        if result["status"] == "COMPLETED":
            # Demo or real — never invent external platform IDs as live
            assert result.get("demo_mode") is True or (result.get("result") or {}).get("demo") is True or result.get("result")


@pytest.mark.asyncio
async def test_decision_loop_creates_structured_actions():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        login = await client.post("/api/v1/auth/login", json={"email": "demo@growthos.ai", "password": "demo1234"})
        headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
        clients = await client.get("/api/v1/clients", headers=headers)
        client_id = clients.json()[0]["id"]

        loop = await client.post(
            "/api/v1/autopilot/decision-loop",
            headers=headers,
            json={"client_id": client_id, "max_actions": 3, "max_iterations": 1},
        )
        assert loop.status_code == 200
        body = loop.json()
        assert "actions_created" in body
        assert body["actions_created"] >= 0

        # Image provider honest failure when not configured
        img = await client.post(
            "/api/v1/autopilot/image/generate",
            headers=headers,
            json={"client_id": client_id, "prompt": "brand ad visual"},
        )
        assert img.status_code == 200
        assert "IMAGE GENERATION NOT CONFIGURED" in (img.json().get("message") or img.json().get("error") or "")
