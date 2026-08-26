from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, EmailStr, Field


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)
    full_name: str = Field(min_length=1, max_length=255)
    organization_name: str = Field(min_length=1, max_length=255)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class TokenResponse(BaseModel):
    access_token: str
    #: Omitted for browser clients: they get the refresh token as an httpOnly
    #: cookie instead, which JavaScript cannot read. Populated only for clients
    #: that asked for body delivery and are not using the cookie.
    refresh_token: str | None = None
    token_type: str = "bearer"


class RefreshRequest(BaseModel):
    """Optional: browsers send the token in an httpOnly cookie instead."""

    refresh_token: str | None = None


class LogoutRequest(BaseModel):
    refresh_token: str | None = None


class SessionOut(BaseModel):
    """An active refresh token, so a user can see and revoke their sessions."""

    id: UUID
    created_at: datetime
    expires_at: datetime
    user_agent: str | None = None


class UserOut(BaseModel):
    id: UUID
    email: EmailStr
    full_name: str
    organization_id: UUID
    organization_name: str
    role: str
    demo_mode: bool  # effective (org OR env)
    organization_demo_mode: bool = True
    operating_mode: str = "DEMO"  # DEMO | LIVE
    env_demo_mode: bool = False

    model_config = {"from_attributes": True}


class OrgModeUpdate(BaseModel):
    demo_mode: bool

