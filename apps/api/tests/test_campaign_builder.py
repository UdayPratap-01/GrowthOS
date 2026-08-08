import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app


@pytest.mark.asyncio
async def test_campaign_build_and_autopilot_run():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test", timeout=60.0) as client:
        login = await client.post("/api/v1/auth/login", json={"email": "demo@growthos.ai", "password": "demo1234"})
        assert login.status_code == 200
        headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

        clients = await client.get("/api/v1/clients", headers=headers)
        assert clients.status_code == 200
        client_id = clients.json()[0]["id"]

        build = await client.post(
            "/api/v1/autopilot/campaigns/build",
            headers=headers,
            json={
                "client_id": client_id,
                "objective": "Generate Leads",
                "budget": 500,
                "duration_days": 30,
                "offer": "Summer Membership",
                "platforms": ["meta", "instagram"],
                "image_quantity": 3,
                "video_quantity": 2,
                "variation_quantity": 5,
            },
        )
        assert build.status_code == 200, build.text
        body = build.json()
        assert "run" in body
        assert body["run"]["status"] in {"AWAITING_APPROVAL", "COMPLETED", "FAILED"}
        assert isinstance(body["action_ids"], list)
        assert "IMAGE GENERATION NOT CONFIGURED" in body["message"] or "VIDEO GENERATION NOT CONFIGURED" in body["message"] or "proposal" in body["message"].lower()
        steps = body["run"]["steps"]
        assert any(s["key"] == "structure" and s["status"] == "completed" for s in steps)
        # Never invent completed live publish
        assert not any(s.get("key") == "publishing" and s.get("status") == "completed" for s in steps)

        library = await client.get(f"/api/v1/autopilot/creative/library?client_id={client_id}", headers=headers)
        assert library.status_code == 200
        assert isinstance(library.json(), list)

        run = await client.post(
            "/api/v1/autopilot/run",
            headers=headers,
            json={
                "client_id": client_id,
                "goal": "Generate Leads",
                "budget": 500,
                "duration_days": 30,
                "platforms": ["meta", "instagram"],
                "autonomy_mode": "copilot",
            },
        )
        assert run.status_code == 200, run.text
        run_body = run.json()
        assert run_body["status"] in {"AWAITING_APPROVAL", "COMPLETED", "FAILED", "RUNNING"}
        assert any(s["key"] == "approval" and s["status"] == "blocked" for s in run_body["steps"])
        publish = next(s for s in run_body["steps"] if s["key"] == "publishing")
        assert publish["status"] != "completed"

        settings = await client.get("/api/v1/autopilot/settings", headers=headers)
        assert settings.status_code == 200
        assert "maximum_actions_per_day" in settings.json()
        assert "max_ai_actions_per_cycle" in settings.json()
