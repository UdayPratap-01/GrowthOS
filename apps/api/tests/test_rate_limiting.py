"""P1-1 — distributed rate limiting."""

from __future__ import annotations

import uuid

import pytest
from httpx import ASGITransport, AsyncClient

from app.core.config import get_settings
from app.core.security import hash_password
from app.db.session import AsyncSessionLocal
from app.main import app
from app.models.client import Client
from app.models.enums import MemberRole
from app.models.organization import Organization, OrganizationMember
from app.models.user import User
from app.security import rate_limit as rl

PASSWORD = "Str0ng-Test-Passw0rd!"


@pytest.fixture(autouse=True)
async def _fresh_backend():
    """Every test starts with an empty limiter and restores the default after."""
    rl.set_rate_limit_backend(rl.InMemoryRateLimitBackend())
    yield
    rl.set_rate_limit_backend(None)


@pytest.fixture
def relax(monkeypatch):
    """Raise limits so unrelated assertions are not throttled."""
    settings = get_settings()
    monkeypatch.setattr(settings, "auth_rate_limit_per_minute", 1000, raising=False)
    monkeypatch.setattr(settings, "auth_rate_limit_per_ip_per_minute", 1000, raising=False)
    return settings


async def _make_user(role: MemberRole = MemberRole.owner) -> tuple[str, uuid.UUID]:
    suffix = uuid.uuid4().hex[:8]
    email = f"rl-{suffix}@ratelimit.test.com"
    async with AsyncSessionLocal() as db:
        user = User(email=email, hashed_password=hash_password(PASSWORD), full_name="RL user")
        org = Organization(name=f"RL {suffix}", slug=f"rl-{suffix}", demo_mode=False)
        db.add_all([user, org])
        await db.flush()
        db.add(OrganizationMember(organization_id=org.id, user_id=user.id, role=role))
        client = Client(organization_id=org.id, business_name="RL Client", industry="saas")
        db.add(client)
        await db.commit()
        return email, client.id


# --------------------------------------------------------------------------
# Backend semantics
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_backend_allows_up_to_limit_then_blocks():
    backend = rl.InMemoryRateLimitBackend()
    policy = rl.RateLimitPolicy("t", limit=3, window_seconds=60)
    results = [await backend.hit("k", policy) for _ in range(4)]
    assert [r.allowed for r in results] == [True, True, True, False]
    assert results[-1].retry_after >= 1


@pytest.mark.asyncio
async def test_backend_isolates_distinct_keys():
    backend = rl.InMemoryRateLimitBackend()
    policy = rl.RateLimitPolicy("t", limit=1, window_seconds=60)
    assert (await backend.hit("a", policy)).allowed
    assert (await backend.hit("b", policy)).allowed
    assert not (await backend.hit("a", policy)).allowed


@pytest.mark.asyncio
async def test_identifiers_are_hashed_not_stored_in_plaintext():
    key = rl.hash_identifier("Victim@Example.com")
    assert "victim" not in key.lower()
    assert "@" not in key
    # Case and whitespace must not create a second bucket.
    assert key == rl.hash_identifier("  victim@example.com  ")


# --------------------------------------------------------------------------
# Shared backend across instances
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_redis_backend_shares_limits_across_api_instances():
    """
    Two RedisRateLimitBackend objects stand in for two API processes. They must
    consume one shared budget, which per-process counters could never do.
    """
    fakeredis = pytest.importorskip("fakeredis")
    server = fakeredis.FakeServer()

    def make_instance():
        backend = rl.RedisRateLimitBackend.__new__(rl.RedisRateLimitBackend)
        backend._redis = fakeredis.aioredis.FakeRedis(server=server, decode_responses=True)
        backend._fallback = rl.InMemoryRateLimitBackend()
        backend._degraded_logged = False
        return backend

    instance_a, instance_b = make_instance(), make_instance()
    policy = rl.RateLimitPolicy("shared", limit=4, window_seconds=60)

    outcomes = []
    for i in range(6):
        target = instance_a if i % 2 == 0 else instance_b
        outcomes.append((await target.hit("same-key", policy)).allowed)

    assert outcomes.count(True) == 4, "the budget must be shared, not doubled"
    assert outcomes[-1] is False


@pytest.mark.asyncio
async def test_redis_outage_degrades_to_local_instead_of_failing_everything():
    class BrokenRedis:
        def pipeline(self, *_a, **_k):
            raise ConnectionError("redis is down")

    backend = rl.RedisRateLimitBackend.__new__(rl.RedisRateLimitBackend)
    backend._redis = BrokenRedis()
    backend._fallback = rl.InMemoryRateLimitBackend()
    backend._degraded_logged = False

    policy = rl.RateLimitPolicy("degraded", limit=2, window_seconds=60)
    assert (await backend.hit("k", policy)).allowed
    assert (await backend.hit("k", policy)).allowed
    assert not (await backend.hit("k", policy)).allowed, "local fallback must still limit"


@pytest.mark.asyncio
async def test_redis_outage_can_fail_closed_when_configured(monkeypatch):
    monkeypatch.setattr(get_settings(), "rate_limit_degrade_to_local", False, raising=False)

    class BrokenRedis:
        def pipeline(self, *_a, **_k):
            raise ConnectionError("redis is down")

    backend = rl.RedisRateLimitBackend.__new__(rl.RedisRateLimitBackend)
    backend._redis = BrokenRedis()
    backend._fallback = rl.InMemoryRateLimitBackend()
    backend._degraded_logged = False

    assert not (await backend.hit("k", rl.RateLimitPolicy("x", 5, 60))).allowed


def test_production_requires_a_shared_backend():
    from app.core.config import Settings
    from app.core.startup_checks import ConfigurationError, validate_configuration

    base = dict(
        environment="production",
        secret_key="9f2c41b7ae0d63528c1fb47e0a95d3ce7b6108fa24d9e35c07bd18492af6c3e5",
        encryption_key="c07bd18492af6c3e59f2c41b7ae0d63528c1fb47e0a95d3ce7b6108fa24d9e35",
        demo_mode=False,
        ai_provider="openai",
        openai_api_key="sk-test",
        database_url="postgresql+asyncpg://u:p@db:5432/growthos",
        api_cors_origins="https://app.example.com",
        storage_backend="s3",
        s3_bucket="growthos-assets",
        metrics_token="test-metrics-token-not-a-placeholder",
        trusted_proxy_ips="none",
    )
    with pytest.raises(ConfigurationError) as exc:
        validate_configuration(Settings(**base, redis_url=""))
    assert "REDIS_URL" in str(exc.value)

    validate_configuration(Settings(**base, redis_url="redis://cache:6379/0"))  # must not raise


# --------------------------------------------------------------------------
# Login brute force
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_repeated_failed_logins_eventually_return_429(monkeypatch):
    """The original audit finding: 25 failed logins all returned 401, never 429."""
    settings = get_settings()
    monkeypatch.setattr(settings, "auth_rate_limit_per_minute", 5, raising=False)
    monkeypatch.setattr(settings, "auth_rate_limit_per_ip_per_minute", 100, raising=False)

    email, _ = await _make_user()
    statuses = []
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as http:
        for _ in range(8):
            r = await http.post("/api/v1/auth/login", json={"email": email, "password": "wrong"})
            statuses.append(r.status_code)

    assert 429 in statuses, f"brute force was never throttled: {statuses}"
    assert statuses[:5] == [401] * 5
    assert statuses[5:] == [429] * 3


@pytest.mark.asyncio
async def test_429_includes_retry_after(monkeypatch):
    monkeypatch.setattr(get_settings(), "auth_rate_limit_per_ip_per_minute", 2, raising=False)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as http:
        for _ in range(2):
            await http.post("/api/v1/auth/login", json={"email": "x@y.com", "password": "p"})
        blocked = await http.post("/api/v1/auth/login", json={"email": "x@y.com", "password": "p"})

    assert blocked.status_code == 429
    assert int(blocked.headers["Retry-After"]) >= 1
    assert blocked.headers["X-RateLimit-Remaining"] == "0"


@pytest.mark.asyncio
async def test_ip_budget_stops_rotating_the_email(monkeypatch):
    """Changing the account identifier must not buy an attacker a fresh budget."""
    settings = get_settings()
    monkeypatch.setattr(settings, "auth_rate_limit_per_minute", 1000, raising=False)
    monkeypatch.setattr(settings, "auth_rate_limit_per_ip_per_minute", 4, raising=False)

    statuses = []
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as http:
        for i in range(7):
            r = await http.post(
                "/api/v1/auth/login", json={"email": f"victim{i}@example.com", "password": "guess"}
            )
            statuses.append(r.status_code)

    assert statuses[-1] == 429, f"IP budget did not hold across identities: {statuses}"


@pytest.mark.asyncio
async def test_rate_limit_does_not_reveal_whether_an_account_exists(monkeypatch):
    """A throttled response must look identical for real and fake accounts."""
    settings = get_settings()
    monkeypatch.setattr(settings, "auth_rate_limit_per_minute", 2, raising=False)
    monkeypatch.setattr(settings, "auth_rate_limit_per_ip_per_minute", 1000, raising=False)

    real_email, _ = await _make_user()
    fake_email = f"ghost-{uuid.uuid4().hex[:8]}@nowhere.test.com"

    async def exhaust(email: str):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as http:
            for _ in range(2):
                await http.post("/api/v1/auth/login", json={"email": email, "password": "wrong"})
            return await http.post("/api/v1/auth/login", json={"email": email, "password": "wrong"})

    real, fake = await exhaust(real_email), await exhaust(fake_email)

    assert real.status_code == fake.status_code == 429

    def comparable(response):
        # The request id is unique per request by design; everything else must match.
        body = response.json()
        body["error"].pop("request_id", None)
        return body

    assert comparable(real) == comparable(fake), "throttled responses must be indistinguishable"


@pytest.mark.asyncio
async def test_legitimate_login_still_works_under_the_limit(relax):
    email, _ = await _make_user()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as http:
        for _ in range(3):
            ok = await http.post("/api/v1/auth/login", json={"email": email, "password": PASSWORD})
            assert ok.status_code == 200, ok.text


@pytest.mark.asyncio
async def test_registration_is_rate_limited(monkeypatch):
    monkeypatch.setattr(get_settings(), "auth_rate_limit_per_ip_per_minute", 3, raising=False)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as http:
        statuses = [
            (
                await http.post(
                    "/api/v1/auth/register",
                    json={
                        "email": f"new-{uuid.uuid4().hex[:8]}@example.com",
                        "password": PASSWORD,
                        "full_name": "New",
                        "organization_name": "New Org",
                    },
                )
            ).status_code
            for _ in range(5)
        ]
    assert 429 in statuses


# --------------------------------------------------------------------------
# Per-account and per-organization isolation
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_one_account_being_throttled_does_not_lock_out_another(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "auth_rate_limit_per_minute", 3, raising=False)
    monkeypatch.setattr(settings, "auth_rate_limit_per_ip_per_minute", 1000, raising=False)

    victim, _ = await _make_user()
    bystander, _ = await _make_user()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as http:
        for _ in range(4):
            await http.post("/api/v1/auth/login", json={"email": victim, "password": "wrong"})
        blocked = await http.post("/api/v1/auth/login", json={"email": victim, "password": PASSWORD})
        other = await http.post("/api/v1/auth/login", json={"email": bystander, "password": PASSWORD})

    assert blocked.status_code == 429
    assert other.status_code == 200, "an unrelated account must not be collateral damage"


@pytest.mark.asyncio
async def test_organization_media_budget_is_enforced_and_tenant_scoped(monkeypatch, relax):
    """Expensive generation is capped per organization, and tenants are independent."""
    monkeypatch.setattr(get_settings(), "media_rate_limit_per_hour", 2, raising=False)

    email_a, client_a = await _make_user()
    email_b, client_b = await _make_user()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as http:
        token_a = (await http.post("/api/v1/auth/login", json={"email": email_a, "password": PASSWORD})).json()
        headers_a = {"Authorization": f"Bearer {token_a['access_token']}"}
        token_b = (await http.post("/api/v1/auth/login", json={"email": email_b, "password": PASSWORD})).json()
        headers_b = {"Authorization": f"Bearer {token_b['access_token']}"}

        body = {"client_id": str(client_a), "prompt": "a product photo", "aspect_ratio": "1:1"}
        codes_a = [
            (await http.post("/api/v1/creative/images/generate", headers=headers_a, json=body)).status_code
            for _ in range(3)
        ]
        code_b = (
            await http.post(
                "/api/v1/creative/images/generate",
                headers=headers_b,
                json={**body, "client_id": str(client_b)},
            )
        ).status_code

    assert codes_a[-1] == 429, f"org media budget not enforced: {codes_a}"
    assert code_b != 429, "another tenant must not be throttled by the first tenant's usage"


@pytest.mark.asyncio
async def test_org_budget_is_not_reset_by_switching_users(monkeypatch, relax):
    """Two members of the same org share one budget."""
    monkeypatch.setattr(get_settings(), "media_rate_limit_per_hour", 2, raising=False)

    suffix = uuid.uuid4().hex[:8]
    async with AsyncSessionLocal() as db:
        org = Organization(name=f"Shared {suffix}", slug=f"shared-{suffix}", demo_mode=False)
        db.add(org)
        await db.flush()
        client = Client(organization_id=org.id, business_name="Shared Client", industry="saas")
        db.add(client)
        emails = []
        for i in range(2):
            email = f"member{i}-{suffix}@shared.test.com"
            user = User(email=email, hashed_password=hash_password(PASSWORD), full_name=f"M{i}")
            db.add(user)
            await db.flush()
            db.add(OrganizationMember(organization_id=org.id, user_id=user.id, role=MemberRole.owner))
            emails.append(email)
        await db.commit()
        client_id = client.id

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as http:
        codes = []
        for i in range(3):
            email = emails[i % 2]
            token = (await http.post("/api/v1/auth/login", json={"email": email, "password": PASSWORD})).json()
            codes.append(
                (
                    await http.post(
                        "/api/v1/creative/images/generate",
                        headers={"Authorization": f"Bearer {token['access_token']}"},
                        json={"client_id": str(client_id), "prompt": "x", "aspect_ratio": "1:1"},
                    )
                ).status_code
            )

    assert codes[-1] == 429, f"rotating users bypassed the org budget: {codes}"


# --------------------------------------------------------------------------
# Coverage
# --------------------------------------------------------------------------


def test_sensitive_endpoints_declare_a_rate_limit():
    import re
    from pathlib import Path

    v1 = Path(__file__).resolve().parents[1] / "app" / "api" / "v1"
    required = {
        ("auth.py", "/login"),
        ("auth.py", "/register"),
        ("webhooks.py", "/meta"),
        ("creative.py", "/images/generate"),
        ("creative.py", "/videos/generate"),
        ("reports.py", "/generate"),
        ("autopilot.py", "/image/generate"),
        ("autopilot.py", "/video/generate"),
        ("autopilot.py", "/run"),
        ("autopilot.py", "/campaigns/build"),
    }
    # Match a `*_limit` dependency anywhere in the decorator — the decorator may
    # be wrapped over several lines, and quota dependencies sit beside it.
    limiter = re.compile(r"Depends\(\w*_limit\)")
    found = set()
    for path in v1.glob("*.py"):
        for block in re.split(r"(?=^@router\.)", path.read_text(encoding="utf-8"), flags=re.M):
            m = re.match(r'@router\.(post|put|patch)\(\s*"([^"]*)"', block)
            if m and limiter.search(block.split("async def")[0]):
                found.add((path.name, m.group(2)))
    missing = required - found
    assert not missing, f"endpoints missing a rate limit: {sorted(missing)}"
