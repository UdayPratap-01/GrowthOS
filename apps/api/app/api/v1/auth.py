from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.deps import AuthContext, get_current_auth
from app.core.mode import effective_demo_mode
from app.core.permissions import Permission, require_permission
from app.db.session import get_db
from app.observability import events, metrics
from app.schemas.auth import (
    LoginRequest,
    LogoutRequest,
    OrgModeUpdate,
    RefreshRequest,
    RegisterRequest,
    SessionOut,
    TokenResponse,
    UserOut,
)
from app.security.rate_limit import auth_rate_limit
from app.services.auth_service import AuthService
from app.services.refresh_token_service import RefreshTokenError, RefreshTokenService

router = APIRouter(prefix="/auth", tags=["auth"])

#: Browsers receive the refresh token only as an httpOnly cookie, so page
#: JavaScript — including anything injected by an XSS — can never read it.
REFRESH_COOKIE = "growthos_refresh"

#: Non-browser clients (CLI, mobile, server-to-server) have nowhere to keep a
#: cookie, so they may ask for the token in the response body by sending
#: `X-Refresh-Token-Delivery: body`. On /auth/refresh the request must also not
#: have presented a cookie: a cookie means a browser, and echoing the token back
#: to a browser would hand injected script the one credential the cookie exists
#: to protect.
REFRESH_DELIVERY_HEADER = "X-Refresh-Token-Delivery"


def _wants_body_delivery(request: Request) -> bool:
    return (request.headers.get(REFRESH_DELIVERY_HEADER) or "").strip().lower() == "body"


def _without_refresh_token(tokens: TokenResponse) -> TokenResponse:
    """Return the pair with the refresh token withheld from the JSON body."""
    return tokens.model_copy(update={"refresh_token": None})


def _set_refresh_cookie(response: Response, token: str) -> None:
    settings = get_settings()
    response.set_cookie(
        REFRESH_COOKIE,
        token,
        max_age=settings.refresh_token_expire_days * 86400,
        httponly=True,
        secure=settings.refresh_cookie_is_secure,
        samesite=settings.refresh_cookie_samesite,
        domain=settings.refresh_cookie_domain or None,
        path="/api/v1/auth",
    )


def _clear_refresh_cookie(response: Response) -> None:
    settings = get_settings()
    response.delete_cookie(
        REFRESH_COOKIE,
        path="/api/v1/auth",
        domain=settings.refresh_cookie_domain or None,
        httponly=True,
        secure=settings.refresh_cookie_is_secure,
        samesite=settings.refresh_cookie_samesite,
    )


def _presented_token(request: Request, body_token: str | None) -> str:
    """Cookie first: if the browser has one, that is the authoritative copy."""
    token = request.cookies.get(REFRESH_COOKIE) or (body_token or "")
    if not token.strip():
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="INVALID_REFRESH_TOKEN: No refresh token was supplied.",
        )
    return token.strip()


@router.post("/register", response_model=TokenResponse, dependencies=[Depends(auth_rate_limit)])
async def register(
    data: RegisterRequest,
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db),
) -> TokenResponse:
    tokens = await AuthService(db).register(data, user_agent=request.headers.get("user-agent"))
    _set_refresh_cookie(response, tokens.refresh_token)
    return tokens if _wants_body_delivery(request) else _without_refresh_token(tokens)


@router.post("/login", response_model=TokenResponse, dependencies=[Depends(auth_rate_limit)])
async def login(
    data: LoginRequest,
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db),
) -> TokenResponse:
    tokens = await AuthService(db).login(data, user_agent=request.headers.get("user-agent"))
    _set_refresh_cookie(response, tokens.refresh_token)
    return tokens if _wants_body_delivery(request) else _without_refresh_token(tokens)


@router.post("/refresh", response_model=TokenResponse, dependencies=[Depends(auth_rate_limit)])
async def refresh(
    request: Request,
    response: Response,
    data: RefreshRequest | None = None,
    db: AsyncSession = Depends(get_db),
) -> TokenResponse:
    """
    Exchange a refresh token for a new pair, consuming the old one.

    Every failure returns the same 401. Distinguishing "expired" from "revoked"
    from "never existed" would tell an attacker holding a stolen token which
    part of the attack worked.
    """
    from_cookie = bool(request.cookies.get(REFRESH_COOKIE))
    presented = _presented_token(request, data.refresh_token if data else None)
    try:
        tokens = await RefreshTokenService(db).rotate(
            presented, user_agent=request.headers.get("user-agent")
        )
    except RefreshTokenError as exc:
        # Commit before raising. Rejection is not a no-op: reuse detection has
        # just revoked the token family, and the request-scoped session rolls
        # back on an exception, which would silently undo it and leave the
        # attacker's token working.
        await db.commit()
        metrics.record_auth(outcome="refresh_rejected")
        events.auth_refresh_rejected(reason=exc.reason)
        _clear_refresh_cookie(response)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="INVALID_REFRESH_TOKEN: Please sign in again.",
        ) from exc

    metrics.record_auth(outcome="refresh")
    _set_refresh_cookie(response, tokens.refresh_token)
    if from_cookie or not _wants_body_delivery(request):
        return _without_refresh_token(tokens)
    return tokens


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    request: Request,
    response: Response,
    data: LogoutRequest | None = None,
    db: AsyncSession = Depends(get_db),
) -> Response:
    """Revoke the presented refresh token. Idempotent, and never 401s."""
    token = request.cookies.get(REFRESH_COOKIE) or (data.refresh_token if data else None)
    if token:
        await RefreshTokenService(db).revoke(token, reason="logout")
    out = Response(status_code=status.HTTP_204_NO_CONTENT)
    _clear_refresh_cookie(out)
    return out


@router.post("/logout-all", status_code=status.HTTP_204_NO_CONTENT)
async def logout_all(
    auth: AuthContext = Depends(get_current_auth),
    db: AsyncSession = Depends(get_db),
) -> Response:
    """End every session for the caller — the control you want after a theft."""
    revoked = await RefreshTokenService(db).revoke_all_for_user(auth.user_id)
    events.auth_sessions_revoked(user_id=auth.user_id, count=revoked)
    out = Response(status_code=status.HTTP_204_NO_CONTENT)
    _clear_refresh_cookie(out)
    return out


@router.get("/sessions", response_model=list[SessionOut])
async def sessions(
    auth: AuthContext = Depends(get_current_auth),
    db: AsyncSession = Depends(get_db),
) -> list[SessionOut]:
    records = await RefreshTokenService(db).active_sessions(auth.user_id)
    return [
        SessionOut(
            id=record.id,
            created_at=record.created_at,
            expires_at=record.expires_at,
            user_agent=record.user_agent,
        )
        for record in records
    ]


@router.get("/me", response_model=UserOut)
async def me(auth: AuthContext = Depends(get_current_auth), db: AsyncSession = Depends(get_db)) -> UserOut:
    return await AuthService(db).me(auth.user_id)


@router.patch("/organization/mode", response_model=UserOut)
async def set_organization_mode(
    data: OrgModeUpdate,
    auth: AuthContext = Depends(require_permission(Permission.organization_manage)),
    db: AsyncSession = Depends(get_db),
) -> UserOut:
    return await AuthService(db).set_org_demo_mode(auth.user_id, data.demo_mode)


@router.get("/operating-mode")
async def operating_mode(auth: AuthContext = Depends(get_current_auth)) -> dict:
    demo = effective_demo_mode(auth.organization)
    return {
        "operating_mode": "DEMO" if demo else "LIVE",
        "organization_demo_mode": auth.organization.demo_mode,
        "env_demo_mode": bool(get_settings().demo_mode),
        "note": (
            "DEMO: seed metrics and simulated executions may be labeled DEMO DATA. "
            "LIVE: never silently invents metrics; remaining seed rows surface as mixed."
            if demo
            else "LIVE: KPIs come from database rows; integrations must confirm external API success."
        ),
    }
