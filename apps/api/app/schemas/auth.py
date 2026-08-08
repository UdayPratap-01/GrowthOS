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
    refresh_token: str
    token_type: str = "bearer"


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

