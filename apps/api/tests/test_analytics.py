import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app


@pytest.mark.asyncio
async def test_analytics_period_and_recommendations():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        login = await client.post("/api/v1/auth/login", json={"email": "demo@growthos.ai", "password": "demo1234"})
        assert login.status_code == 200
        token = login.json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}

        analytics = await client.get("/api/v1/analytics?period_days=30", headers=headers)
        assert analytics.status_code == 200, analytics.text
        body = analytics.json()
        assert body["period_days"] == 30
        assert "series" in body
        assert "leads" in body["series"]
        assert body["data_source"] in {"demo", "live", "mixed"}

        recs = await client.post("/api/v1/recommendations/generate", headers=headers, json={})
        assert recs.status_code == 200, recs.text
        assert isinstance(recs.json(), list)
        assert len(recs.json()) >= 1
        first = recs.json()[0]
        assert "evidence" in first
        assert "recommendation" in first
