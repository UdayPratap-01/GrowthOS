from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import AuthContext, get_current_auth
from app.core.mode import effective_demo_mode
from app.db.session import get_db
from app.schemas.auth import LoginRequest, OrgModeUpdate, RegisterRequest, TokenResponse, UserOut
from app.services.auth_service import AuthService

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/register", response_model=TokenResponse)
async def register(data: RegisterRequest, db: AsyncSession = Depends(get_db)) -> TokenResponse:
    return await AuthService(db).register(data)


@router.post("/login", response_model=TokenResponse)
async def login(data: LoginRequest, db: AsyncSession = Depends(get_db)) -> TokenResponse:
    return await AuthService(db).login(data)


@router.get("/me", response_model=UserOut)
async def me(auth: AuthContext = Depends(get_current_auth), db: AsyncSession = Depends(get_db)) -> UserOut:
    return await AuthService(db).me(auth.user_id)


@router.patch("/organization/mode", response_model=UserOut)
async def set_organization_mode(
    data: OrgModeUpdate,
    auth: AuthContext = Depends(get_current_auth),
    db: AsyncSession = Depends(get_db),
) -> UserOut:
    return await AuthService(db).set_org_demo_mode(auth.user_id, data.demo_mode)


@router.get("/operating-mode")
async def operating_mode(auth: AuthContext = Depends(get_current_auth)) -> dict:
    from app.core.config import get_settings

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
