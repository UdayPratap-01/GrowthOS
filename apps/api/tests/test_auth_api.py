import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app


@pytest.mark.asyncio
async def test_login_and_me():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        login = await client.post("/api/v1/auth/login", json={"email": "demo@growthos.ai", "password": "demo1234"})
        assert login.status_code == 200, login.text
        token = login.json()["access_token"]
        me = await client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
        assert me.status_code == 200
        body = me.json()
        assert body["email"] == "demo@growthos.ai"
        assert body["demo_mode"] is True
