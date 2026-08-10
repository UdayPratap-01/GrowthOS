"""P1-6 — refresh token lifecycle: rotation, revocation, reuse detection."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.core.config import get_settings
from app.core.security import hash_password, safe_decode_token
from app.db.session import AsyncSessionLocal
from app.main import app
from app.models.auth_tokens import RefreshToken
from app.models.enums import MemberRole
from app.models.organization import Organization, OrganizationMember
from app.models.user import User
from app.services.refresh_token_service import (
    RefreshTokenError,
    RefreshTokenService,
    hash_token,
)

PASSWORD = "Str0ng-Test-Passw0rd!"


@pytest.fixture
async def account():
    suffix = uuid.uuid4().hex[:8]
    email = f"refresh-{suffix}@example.com"
    async with AsyncSessionLocal() as db:
        org = Organization(name=f"Refresh {suffix}", slug=f"refresh-{suffix}", demo_mode=False)
        user = User(email=email, hashed_password=hash_password(PASSWORD), full_name="Refresh")
        db.add_all([org, user])
        await db.flush()
        db.add(OrganizationMember(organization_id=org.id, user_id=user.id, role=MemberRole.owner))
        await db.commit()
        return {"email": email, "user_id": user.id, "org_id": org.id}


#: Browsers only ever receive the refresh token as an httpOnly cookie. Tests that
#: need the raw token identify themselves as non-browser clients, exactly as a
#: CLI or mobile client would.
BODY_DELIVERY = {"X-Refresh-Token-Delivery": "body"}


async def login(http: AsyncClient, email: str) -> dict:
    response = await http.post(
        "/api/v1/auth/login",
        json={"email": email, "password": PASSWORD},
        headers=BODY_DELIVERY,
    )
    assert response.status_code == 200, response.text
    return response.json()


def client() -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


# --------------------------------------------------------------------------
# Storage
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_raw_token_is_never_stored(account):
    async with client() as http:
        tokens = await login(http, account["email"])

    async with AsyncSessionLocal() as db:
        rows = list(
            await db.scalars(select(RefreshToken).where(RefreshToken.user_id == account["user_id"]))
        )
    assert len(rows) == 1
    assert rows[0].token_hash == hash_token(tokens["refresh_token"])
    assert tokens["refresh_token"] not in {r.token_hash for r in rows}
    assert len(rows[0].token_hash) == 64


@pytest.mark.asyncio
async def test_refresh_token_is_opaque_not_a_signed_jwt(account):
    """A JWT would remain valid after revocation; that is the bug being fixed."""
    async with client() as http:
        tokens = await login(http, account["email"])
    assert safe_decode_token(tokens["refresh_token"]) is None
    assert tokens["refresh_token"].count(".") != 2


@pytest.mark.asyncio
async def test_login_sets_an_httponly_refresh_cookie(account):
    async with client() as http:
        response = await http.post(
            "/api/v1/auth/login", json={"email": account["email"], "password": PASSWORD}
        )
    cookie = response.headers.get("set-cookie", "")
    assert "growthos_refresh=" in cookie
    assert "HttpOnly" in cookie
    assert "Path=/api/v1/auth" in cookie


def test_the_cookie_is_secure_outside_development():
    from app.core.config import Settings

    assert Settings(environment="production").refresh_cookie_is_secure is True
    assert Settings(environment="staging").refresh_cookie_is_secure is True
    assert Settings(environment="development").refresh_cookie_is_secure is False


# --------------------------------------------------------------------------
# Exposure to browser JavaScript
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_login_does_not_return_the_refresh_token_to_a_browser(account):
    """
    A browser gets the cookie and nothing else. Anything returned in the body
    is readable by injected script and, in the reported finding, ended up in
    localStorage where it survives the page.
    """
    async with client() as http:
        response = await http.post(
            "/api/v1/auth/login", json={"email": account["email"], "password": PASSWORD}
        )

    body = response.json()
    assert response.status_code == 200
    assert body["access_token"], "the short-lived token still comes back"
    assert body.get("refresh_token") is None
    assert "growthos_refresh=" in response.headers.get("set-cookie", "")


@pytest.mark.asyncio
async def test_register_does_not_return_the_refresh_token_to_a_browser():
    suffix = uuid.uuid4().hex[:8]
    async with client() as http:
        response = await http.post(
            "/api/v1/auth/register",
            json={
                "email": f"reg-{suffix}@example.com",
                "password": PASSWORD,
                "full_name": "Reg Test",
                "organization_name": f"Reg {suffix}",
            },
        )
    assert response.status_code == 200, response.text
    assert response.json().get("refresh_token") is None
    assert "growthos_refresh=" in response.headers.get("set-cookie", "")


@pytest.mark.asyncio
async def test_a_cookie_refresh_never_echoes_the_token_even_if_asked(account):
    """
    The XSS case. Injected script can send any header it likes and the cookie
    rides along automatically — so the opt-in must not be enough on its own.
    """
    async with client() as http:
        await login(http, account["email"])
        http.cookies.clear()
        # Re-establish a browser session, this time without asking for the body.
        await http.post(
            "/api/v1/auth/login", json={"email": account["email"], "password": PASSWORD}
        )
        response = await http.post("/api/v1/auth/refresh", headers=BODY_DELIVERY)

    assert response.status_code == 200, response.text
    assert response.json().get("refresh_token") is None


@pytest.mark.asyncio
async def test_a_non_browser_client_can_still_be_given_the_token(account):
    """CLI and mobile clients have nowhere to keep a cookie."""
    async with client() as http:
        tokens = await login(http, account["email"])
    assert tokens["refresh_token"], "opt-in body delivery must keep working"


@pytest.mark.asyncio
async def test_a_browser_session_renews_on_the_cookie_alone(account):
    """Session renewal must not depend on the token the browser no longer has."""
    async with client() as http:
        first = await http.post(
            "/api/v1/auth/login", json={"email": account["email"], "password": PASSWORD}
        )
        assert first.json().get("refresh_token") is None

        seen = set()
        for _ in range(3):
            renewed = await http.post("/api/v1/auth/refresh")
            assert renewed.status_code == 200, renewed.text
            access = renewed.json()["access_token"]
            seen.add(access)
            me = await http.get(
                "/api/v1/auth/me", headers={"Authorization": f"Bearer {access}"}
            )
            assert me.status_code == 200

    assert seen, "each renewal must produce a usable access token"


@pytest.mark.asyncio
async def test_a_browser_logout_revokes_the_session_behind_the_cookie(account):
    async with client() as http:
        await http.post(
            "/api/v1/auth/login", json={"email": account["email"], "password": PASSWORD}
        )
        logout = await http.post("/api/v1/auth/logout")
        assert logout.status_code == 204
        after = await http.post("/api/v1/auth/refresh")

    assert after.status_code == 401, "the cookie session must be dead after logout"


def test_the_frontend_never_stores_a_refresh_token():
    """
    The finding was in the client, so the assertion belongs there too: no code
    path may write the refresh token into web storage.
    """
    from pathlib import Path

    web = Path(__file__).resolve().parents[3] / "apps" / "web" / "src"
    offenders = []
    for source in web.rglob("*.ts*"):
        text = source.read_text(encoding="utf-8")
        for line in text.splitlines():
            if "growthos_refresh_token" not in line:
                continue
            # Removing the legacy key is how an upgraded browser stops carrying one.
            if "removeItem" in line:
                continue
            offenders.append(f"{source.relative_to(web)}: {line.strip()}")
    assert not offenders, f"refresh token reachable from JavaScript: {offenders}"


# --------------------------------------------------------------------------
# Rotation
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_valid_refresh_returns_a_new_pair(account):
    async with client() as http:
        tokens = await login(http, account["email"])
        http.cookies.clear()
        response = await http.post(
            "/api/v1/auth/refresh",
            json={"refresh_token": tokens["refresh_token"]},
            headers=BODY_DELIVERY,
        )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["access_token"]
    assert body["refresh_token"] != tokens["refresh_token"], "the token must rotate"


@pytest.mark.asyncio
async def test_the_new_access_token_actually_works(account):
    async with client() as http:
        tokens = await login(http, account["email"])
        http.cookies.clear()
        refreshed = (
            await http.post(
                "/api/v1/auth/refresh",
                json={"refresh_token": tokens["refresh_token"]},
                headers=BODY_DELIVERY,
            )
        ).json()
        me = await http.get(
            "/api/v1/auth/me", headers={"Authorization": f"Bearer {refreshed['access_token']}"}
        )
    assert me.status_code == 200
    assert me.json()["email"] == account["email"]


@pytest.mark.asyncio
async def test_the_old_token_stops_working_after_rotation(account):
    async with client() as http:
        tokens = await login(http, account["email"])
        http.cookies.clear()
        await http.post("/api/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]})
        # Drop the cookie the refresh just set, or it would be used in preference
        # to the body and this would exercise the wrong path.
        http.cookies.clear()
        replayed = await http.post(
            "/api/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]}
        )
    assert replayed.status_code == 401


@pytest.mark.asyncio
async def test_the_cookie_alone_is_enough_to_refresh(account):
    """A browser client never has to touch the token."""
    async with client() as http:
        await login(http, account["email"])
        response = await http.post("/api/v1/auth/refresh")
    assert response.status_code == 200
    assert "growthos_refresh=" in response.headers.get("set-cookie", "")


@pytest.mark.asyncio
async def test_rotation_chain_stays_in_one_family(account):
    async with client() as http:
        tokens = await login(http, account["email"])
        current = tokens["refresh_token"]
        for _ in range(3):
            # Dropping the cookie each time keeps this on the non-browser path;
            # otherwise the cookie set by the previous response would be used.
            http.cookies.clear()
            current = (
                await http.post(
                    "/api/v1/auth/refresh",
                    json={"refresh_token": current},
                    headers=BODY_DELIVERY,
                )
            ).json()["refresh_token"]

    async with AsyncSessionLocal() as db:
        rows = list(
            await db.scalars(select(RefreshToken).where(RefreshToken.user_id == account["user_id"]))
        )
    assert len(rows) == 4
    assert len({r.family_id for r in rows}) == 1, "rotation must not start a new family"
    assert sum(1 for r in rows if r.revoked_at is None) == 1, "only the newest stays live"
    assert [r.replaced_by_id for r in rows if r.revoked_reason == "rotated"].count(None) == 0


# --------------------------------------------------------------------------
# Rejection
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_invalid_token_is_rejected():
    async with client() as http:
        response = await http.post("/api/v1/auth/refresh", json={"refresh_token": "not-a-token"})
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_missing_token_is_rejected():
    async with client() as http:
        response = await http.post("/api/v1/auth/refresh", json={})
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_expired_token_is_rejected(account):
    async with AsyncSessionLocal() as db:
        raw, record = await RefreshTokenService(db).issue(account["user_id"])
        record.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        await db.commit()

    async with client() as http:
        response = await http.post("/api/v1/auth/refresh", json={"refresh_token": raw})
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_revoked_token_is_rejected(account):
    async with AsyncSessionLocal() as db:
        service = RefreshTokenService(db)
        raw, _ = await service.issue(account["user_id"])
        await service.revoke(raw)
        await db.commit()

    async with client() as http:
        response = await http.post("/api/v1/auth/refresh", json={"refresh_token": raw})
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_every_rejection_looks_identical(account):
    """Differing responses would tell an attacker which token they hold."""
    async with AsyncSessionLocal() as db:
        service = RefreshTokenService(db)
        revoked, _ = await service.issue(account["user_id"])
        await service.revoke(revoked)
        expired_raw, expired = await service.issue(account["user_id"])
        expired.expires_at = datetime.now(timezone.utc) - timedelta(days=1)
        await db.commit()

    async with client() as http:
        bodies, statuses = [], []
        for token in ("totally-made-up", revoked, expired_raw):
            response = await http.post("/api/v1/auth/refresh", json={"refresh_token": token})
            statuses.append(response.status_code)
            body = response.json()
            body["error"].pop("request_id", None)
            bodies.append(body)

    assert statuses == [401, 401, 401]
    assert bodies[0] == bodies[1] == bodies[2]


@pytest.mark.asyncio
async def test_deactivated_user_cannot_refresh(account):
    async with AsyncSessionLocal() as db:
        raw, _ = await RefreshTokenService(db).issue(account["user_id"])
        user = await db.get(User, account["user_id"])
        user.is_active = False
        await db.commit()

    async with client() as http:
        response = await http.post("/api/v1/auth/refresh", json={"refresh_token": raw})
    assert response.status_code == 401

    async with AsyncSessionLocal() as db:
        live = list(
            await db.scalars(
                select(RefreshToken).where(
                    RefreshToken.user_id == account["user_id"], RefreshToken.revoked_at.is_(None)
                )
            )
        )
    assert live == [], "deactivation must not leave a usable session"


# --------------------------------------------------------------------------
# Reuse detection
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_replaying_a_rotated_token_kills_the_whole_family(account):
    """
    The stolen-token scenario: the thief refreshes, then the real user's client
    presents the token it still holds. Neither can be identified, so both lose
    the session.
    """
    async with AsyncSessionLocal() as db:
        service = RefreshTokenService(db)
        original, _ = await service.issue(account["user_id"])
        rotated = await service.rotate(original)
        await db.commit()

    async with AsyncSessionLocal() as db:
        with pytest.raises(RefreshTokenError) as caught:
            await RefreshTokenService(db).rotate(original)
        await db.commit()
    assert caught.value.reason == "reuse_detected"

    async with client() as http:
        after = await http.post(
            "/api/v1/auth/refresh", json={"refresh_token": rotated.refresh_token}
        )
    assert after.status_code == 401, "the successor must be revoked too"


@pytest.mark.asyncio
async def test_reuse_detection_survives_the_rejected_request(account):
    """
    The rejection raises, and a request-scoped session rolls back on an
    exception — so the revocation must be committed deliberately or the
    attacker's token keeps working.
    """
    async with client() as http:
        tokens = await login(http, account["email"])
        http.cookies.clear()
        successor = (
            await http.post(
                "/api/v1/auth/refresh",
                json={"refresh_token": tokens["refresh_token"]},
                headers=BODY_DELIVERY,
            )
        ).json()["refresh_token"]

        http.cookies.clear()
        replay = await http.post(
            "/api/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]}
        )
        assert replay.status_code == 401

        http.cookies.clear()
        after = await http.post("/api/v1/auth/refresh", json={"refresh_token": successor})

    assert after.status_code == 401, "the successor must be dead after reuse was detected"

    async with AsyncSessionLocal() as db:
        live = list(
            await db.scalars(
                select(RefreshToken).where(
                    RefreshToken.user_id == account["user_id"], RefreshToken.revoked_at.is_(None)
                )
            )
        )
    assert live == []


@pytest.mark.asyncio
async def test_reuse_does_not_affect_other_sessions(account):
    """A phone being compromised must not sign the user out of their laptop."""
    async with AsyncSessionLocal() as db:
        service = RefreshTokenService(db)
        phone, _ = await service.issue(account["user_id"])
        laptop, _ = await service.issue(account["user_id"])
        await service.rotate(phone)
        await db.commit()

    async with AsyncSessionLocal() as db:
        with pytest.raises(RefreshTokenError):
            await RefreshTokenService(db).rotate(phone)
        await db.commit()

    async with client() as http:
        response = await http.post("/api/v1/auth/refresh", json={"refresh_token": laptop})
    assert response.status_code == 200, "the other session must survive"


@pytest.mark.asyncio
async def test_reuse_is_logged(account, caplog):
    import logging

    async with AsyncSessionLocal() as db:
        service = RefreshTokenService(db)
        raw, _ = await service.issue(account["user_id"])
        await service.rotate(raw)
        await db.commit()

    with caplog.at_level(logging.WARNING, logger="growthos.auth"):
        async with AsyncSessionLocal() as db:
            with pytest.raises(RefreshTokenError):
                await RefreshTokenService(db).rotate(raw)

    record = next(r for r in caplog.records if getattr(r, "event", None) == "auth.refresh_reuse")
    assert record.auth_user_id == str(account["user_id"])


# --------------------------------------------------------------------------
# Logout
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_logout_revokes_the_token(account):
    async with client() as http:
        tokens = await login(http, account["email"])
        http.cookies.clear()
        logout = await http.post("/api/v1/auth/logout", json={"refresh_token": tokens["refresh_token"]})
        assert logout.status_code == 204
        after = await http.post(
            "/api/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]}
        )
    assert after.status_code == 401


@pytest.mark.asyncio
async def test_logout_clears_the_cookie(account):
    async with client() as http:
        await login(http, account["email"])
        response = await http.post("/api/v1/auth/logout")
    assert response.status_code == 204
    assert "growthos_refresh=" in response.headers.get("set-cookie", "")
    assert 'Max-Age=0' in response.headers["set-cookie"] or 'expires=' in response.headers["set-cookie"].lower()


@pytest.mark.asyncio
async def test_logout_is_idempotent_and_never_leaks_existence():
    async with client() as http:
        first = await http.post("/api/v1/auth/logout", json={"refresh_token": "never-existed"})
        second = await http.post("/api/v1/auth/logout", json={"refresh_token": "never-existed"})
    assert first.status_code == second.status_code == 204


@pytest.mark.asyncio
async def test_logout_all_ends_every_session(account):
    async with AsyncSessionLocal() as db:
        service = RefreshTokenService(db)
        first, _ = await service.issue(account["user_id"])
        second, _ = await service.issue(account["user_id"])
        await db.commit()

    async with client() as http:
        tokens = await login(http, account["email"])
        response = await http.post(
            "/api/v1/auth/logout-all",
            headers={"Authorization": f"Bearer {tokens['access_token']}"},
        )
        assert response.status_code == 204
        http.cookies.clear()
        for token in (first, second, tokens["refresh_token"]):
            assert (
                await http.post("/api/v1/auth/refresh", json={"refresh_token": token})
            ).status_code == 401


@pytest.mark.asyncio
async def test_sessions_endpoint_lists_only_live_sessions(account):
    async with AsyncSessionLocal() as db:
        service = RefreshTokenService(db)
        revoked, _ = await service.issue(account["user_id"])
        await service.revoke(revoked)
        await service.issue(account["user_id"])
        await db.commit()

    async with client() as http:
        tokens = await login(http, account["email"])
        listing = await http.get(
            "/api/v1/auth/sessions", headers={"Authorization": f"Bearer {tokens['access_token']}"}
        )

    assert listing.status_code == 200
    # The extra live token, plus the one just created by logging in.
    assert len(listing.json()) == 2


@pytest.mark.asyncio
async def test_sessions_are_not_visible_across_users(account):
    other = uuid.uuid4().hex[:8]
    async with AsyncSessionLocal() as db:
        org = Organization(name=f"Other {other}", slug=f"other-{other}", demo_mode=False)
        user = User(
            email=f"other-{other}@example.com",
            hashed_password=hash_password(PASSWORD),
            full_name="Other",
        )
        db.add_all([org, user])
        await db.flush()
        db.add(OrganizationMember(organization_id=org.id, user_id=user.id, role=MemberRole.owner))
        await RefreshTokenService(db).issue(account["user_id"])
        await db.commit()
        other_email = user.email

    async with client() as http:
        tokens = await login(http, other_email)
        listing = await http.get(
            "/api/v1/auth/sessions", headers={"Authorization": f"Bearer {tokens['access_token']}"}
        )

    assert [item["id"] for item in listing.json()]  # own session only
    assert len(listing.json()) == 1


# --------------------------------------------------------------------------
# Session length
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_session_outlives_the_access_token(account):
    """
    The reported symptom was a hard logout at ~60 minutes. The refresh token
    lives for days, so an expired access token is recoverable without a login.
    """
    settings = get_settings()
    assert settings.refresh_token_expire_days * 24 * 60 > settings.access_token_expire_minutes

    async with AsyncSessionLocal() as db:
        raw, record = await RefreshTokenService(db).issue(account["user_id"])
        expires_at = record.expires_at
        await db.commit()

    expected = datetime.now(timezone.utc) + timedelta(days=settings.refresh_token_expire_days)
    stored = expires_at if expires_at.tzinfo else expires_at.replace(tzinfo=timezone.utc)
    assert abs((stored - expected).total_seconds()) < 60

    async with client() as http:
        assert (
            await http.post("/api/v1/auth/refresh", json={"refresh_token": raw})
        ).status_code == 200


@pytest.mark.asyncio
async def test_access_token_carries_the_user_and_expiry(account):
    async with client() as http:
        tokens = await login(http, account["email"])
    claims = safe_decode_token(tokens["access_token"])
    assert claims["sub"] == str(account["user_id"])
    assert claims["type"] == "access"
    assert claims["exp"] > datetime.now(timezone.utc).timestamp()


@pytest.mark.asyncio
async def test_expired_rows_can_be_purged(account):
    async with AsyncSessionLocal() as db:
        service = RefreshTokenService(db)
        _, old = await service.issue(account["user_id"])
        old.expires_at = datetime.now(timezone.utc) - timedelta(days=90)
        live_raw, _ = await service.issue(account["user_id"])
        await db.commit()

    async with AsyncSessionLocal() as db:
        removed = await RefreshTokenService(db).purge_expired(older_than_days=30)
        await db.commit()
    assert removed == 1

    async with client() as http:
        assert (
            await http.post("/api/v1/auth/refresh", json={"refresh_token": live_raw})
        ).status_code == 200
