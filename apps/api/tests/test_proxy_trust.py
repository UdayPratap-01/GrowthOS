"""
X-Forwarded-For must not be taken on faith.

Every IP-keyed control — login brute-force limits, webhook limits, request logs
— is only as trustworthy as the address it is keyed on. If any client can name
its own address, the attacker simply changes it every request and the limit
stops existing.

The rule implemented here: the header is read only when the request arrived from
a peer listed in TRUSTED_PROXY_IPS, and then only the part of the chain that our
own proxies appended can be believed.
"""

from __future__ import annotations

import uuid

import pytest
from httpx import ASGITransport, AsyncClient

from app.core.config import Settings
from app.core.startup_checks import ConfigurationError, validate_configuration
from app.main import app
from app.security import rate_limit as rl
from app.security.rate_limit import InMemoryRateLimitBackend, client_ip, set_rate_limit_backend


class _Request:
    """The two things `client_ip` reads, without standing up a server."""

    def __init__(self, peer: str | None, forwarded: str | None = None) -> None:
        self.client = type("Peer", (), {"host": peer})() if peer else None
        self.headers = {"x-forwarded-for": forwarded} if forwarded else {}


@pytest.fixture(autouse=True)
def _clear_matcher_cache():
    rl._proxy_matcher.cache_clear()
    yield
    rl._proxy_matcher.cache_clear()


def _with_trust(monkeypatch, value: str) -> None:
    settings = Settings(trusted_proxy_ips=value)
    monkeypatch.setattr(rl, "get_settings", lambda: settings)


# --------------------------------------------------------------------------
# Unconfigured: the header is ignored
# --------------------------------------------------------------------------


def test_forwarded_header_is_ignored_when_no_proxy_is_configured(monkeypatch):
    _with_trust(monkeypatch, "")
    assert client_ip(_Request("203.0.113.9", "1.2.3.4")) == "203.0.113.9"


def test_explicit_none_also_ignores_the_header(monkeypatch):
    _with_trust(monkeypatch, "none")
    assert client_ip(_Request("203.0.113.9", "1.2.3.4")) == "203.0.113.9"


def test_the_socket_peer_is_used_when_there_is_no_header(monkeypatch):
    _with_trust(monkeypatch, "10.0.0.1")
    assert client_ip(_Request("10.0.0.1")) == "10.0.0.1"


def test_a_missing_peer_degrades_to_a_constant(monkeypatch):
    _with_trust(monkeypatch, "")
    assert client_ip(_Request(None)) == "unknown"


# --------------------------------------------------------------------------
# Configured: only our own proxies are believed
# --------------------------------------------------------------------------


def test_a_trusted_proxy_may_report_the_client(monkeypatch):
    _with_trust(monkeypatch, "10.0.0.1")
    assert client_ip(_Request("10.0.0.1", "198.51.100.7")) == "198.51.100.7"


def test_a_cidr_block_covers_its_members(monkeypatch):
    _with_trust(monkeypatch, "10.0.0.0/8")
    assert client_ip(_Request("10.11.12.13", "198.51.100.7")) == "198.51.100.7"


def test_an_untrusted_peer_cannot_claim_another_address(monkeypatch):
    """The whole finding, in one assertion."""
    _with_trust(monkeypatch, "10.0.0.0/8")
    assert client_ip(_Request("203.0.113.9", "198.51.100.7")) == "203.0.113.9"


def test_only_hops_appended_by_our_proxies_are_believed(monkeypatch):
    """
    The caller controls the left of the chain. With two of our proxies in front,
    the real client is the last entry we did not add ourselves.
    """
    _with_trust(monkeypatch, "10.0.0.0/8")
    forwarded = "1.1.1.1, 198.51.100.7, 10.0.0.5"
    assert client_ip(_Request("10.0.0.9", forwarded)) == "198.51.100.7"


def test_a_forged_prefix_cannot_shift_the_key(monkeypatch):
    """
    Two requests from the same client with different forged prefixes must key
    identically, or the limit is per-header rather than per-client.
    """
    _with_trust(monkeypatch, "10.0.0.0/8")
    first = client_ip(_Request("10.0.0.9", "9.9.9.9, 198.51.100.7"))
    second = client_ip(_Request("10.0.0.9", "8.8.8.8, 198.51.100.7"))
    assert first == second == "198.51.100.7"


def test_a_chain_of_only_our_own_proxies_falls_back_to_the_peer(monkeypatch):
    _with_trust(monkeypatch, "10.0.0.0/8")
    assert client_ip(_Request("10.0.0.9", "10.0.0.5, 10.0.0.7")) == "10.0.0.9"


def test_garbage_in_the_header_is_not_treated_as_an_address(monkeypatch):
    _with_trust(monkeypatch, "10.0.0.0/8")
    # Unparseable entries are "not one of ours", so the first is returned as the
    # client — it is still bounded by having come through a trusted proxy.
    assert client_ip(_Request("10.0.0.9", "not-an-ip")) == "not-an-ip"


def test_an_invalid_trust_entry_is_dropped_rather_than_widening_trust(monkeypatch):
    _with_trust(monkeypatch, "not-a-cidr")
    assert client_ip(_Request("203.0.113.9", "198.51.100.7")) == "203.0.113.9"


def test_ipv6_proxies_are_supported(monkeypatch):
    _with_trust(monkeypatch, "2001:db8::/32")
    assert client_ip(_Request("2001:db8::1", "198.51.100.7")) == "198.51.100.7"


def test_wildcard_trusts_everyone_for_local_development(monkeypatch):
    _with_trust(monkeypatch, "*")
    assert client_ip(_Request("172.18.0.4", "198.51.100.7")) == "198.51.100.7"


# --------------------------------------------------------------------------
# Production configuration
# --------------------------------------------------------------------------


def _prod(**overrides) -> Settings:
    base = dict(
        environment="production",
        secret_key="9f2c41b7ae0d63528c1fb47e0a95d3ce7b6108fa24d9e35c07bd18492af6c3e5",
        encryption_key="c07bd18492af6c3e59f2c41b7ae0d63528c1fb47e0a95d3ce7b6108fa24d9e35",
        demo_mode=False,
        ai_provider="openai",
        openai_api_key="sk-live-test",
        database_url="postgresql+asyncpg://u:p@db:5432/growthos",
        api_cors_origins="https://app.example.com",
        redis_url="redis://cache:6379/0",
        storage_backend="s3",
        s3_bucket="growthos-assets",
        metrics_token="metrics-token-not-a-placeholder-value",
        trusted_proxy_ips="10.0.0.0/8",
        inline_job_execution=False,
        db_auto_create=False,
        allow_demo_seed=False,
    )
    base.update(overrides)
    return Settings(**base)


def test_production_refuses_an_unset_proxy_configuration():
    with pytest.raises(ConfigurationError) as exc:
        validate_configuration(_prod(trusted_proxy_ips=""))
    assert "TRUSTED_PROXY_IPS" in str(exc.value)


def test_production_refuses_wildcard_trust():
    with pytest.raises(ConfigurationError) as exc:
        validate_configuration(_prod(trusted_proxy_ips="*"))
    assert "TRUSTED_PROXY_IPS" in str(exc.value)


def test_production_refuses_an_unparseable_entry():
    with pytest.raises(ConfigurationError):
        validate_configuration(_prod(trusted_proxy_ips="10.0.0.0/8,potato"))


def test_production_accepts_an_explicit_direct_exposure():
    validate_configuration(_prod(trusted_proxy_ips="none"))


def test_development_needs_no_proxy_configuration():
    validate_configuration(
        Settings(
            environment="development",
            demo_mode=True,
            ai_provider="mock",
            storage_backend="local",
            redis_url="",
            metrics_token="",
            trusted_proxy_ips="",
            database_url="sqlite+aiosqlite:///./growthos.db",
        )
    )


# --------------------------------------------------------------------------
# End to end: the limiter itself
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rotating_the_header_does_not_buy_extra_login_attempts(monkeypatch):
    """
    An attacker with one IP sends a different X-Forwarded-For every time. With
    no trusted proxy configured, all of it lands on one bucket and the limit
    still bites.
    """
    settings = Settings(trusted_proxy_ips="", auth_rate_limit_per_ip_per_minute=5)
    monkeypatch.setattr(rl, "get_settings", lambda: settings)
    set_rate_limit_backend(InMemoryRateLimitBackend())

    statuses = []
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as http:
        for index in range(12):
            resp = await http.post(
                "/api/v1/auth/login",
                json={"email": f"nobody-{uuid.uuid4().hex[:6]}@example.com", "password": "wrong"},
                headers={"X-Forwarded-For": f"203.0.113.{index}"},
            )
            statuses.append(resp.status_code)

    set_rate_limit_backend(None)
    assert 429 in statuses, "spoofing the header must not reset the per-IP budget"
