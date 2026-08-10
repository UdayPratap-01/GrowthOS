import re
from datetime import datetime, timezone
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.config import get_settings
from app.core.security import hash_password, verify_password
from app.models.ai_ops import Subscription
from app.models.enums import MemberRole
from app.models.organization import Organization, OrganizationMember
from app.models.user import User
from app.schemas.auth import LoginRequest, RegisterRequest, TokenResponse, UserOut
from app.observability import events, metrics
from app.security.audit import write_audit
from app.services.refresh_token_service import RefreshTokenService


def slugify(value: str) -> str:
    value = value.lower().strip()
    value = re.sub(r"[^a-z0-9]+", "-", value)
    return value.strip("-")[:100] or "org"


class AuthService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def register(self, data: RegisterRequest, *, user_agent: str | None = None) -> TokenResponse:
        existing = await self.db.scalar(select(User).where(User.email == data.email.lower()))
        if existing:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Email already registered")

        settings = get_settings()
        base_slug = slugify(data.organization_name)
        slug = base_slug
        i = 1
        while await self.db.scalar(select(Organization).where(Organization.slug == slug)):
            slug = f"{base_slug}-{i}"
            i += 1

        user = User(
            email=data.email.lower(),
            hashed_password=hash_password(data.password),
            full_name=data.full_name,
            last_login_at=datetime.now(timezone.utc),
        )
        org = Organization(name=data.organization_name, slug=slug, demo_mode=settings.demo_mode)
        self.db.add(user)
        self.db.add(org)
        await self.db.flush()
        self.db.add(OrganizationMember(organization_id=org.id, user_id=user.id, role=MemberRole.owner))
        self.db.add(Subscription(organization_id=org.id, plan="starter", status="active"))
        await write_audit(self.db, action="auth.register", organization_id=org.id, user_id=user.id)
        await self.db.flush()
        return await RefreshTokenService(self.db).issue_pair(user.id, user_agent=user_agent)

    async def login(self, data: LoginRequest, *, user_agent: str | None = None) -> TokenResponse:
        user = await self.db.scalar(select(User).where(User.email == data.email.lower()))
        if not user or not verify_password(data.password, user.hashed_password):
            # One branch for both "no such user" and "wrong password": the log
            # records a hashed email, and the caller gets an identical response,
            # so neither reveals whether the account exists.
            events.auth_failure(email=data.email, reason="invalid_credentials")
            metrics.record_auth(outcome="failure")
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")
        user.last_login_at = datetime.now(timezone.utc)
        membership = await self.db.scalar(select(OrganizationMember).where(OrganizationMember.user_id == user.id))
        await write_audit(
            self.db,
            action="auth.login",
            organization_id=membership.organization_id if membership else None,
            user_id=user.id,
        )
        events.auth_success(
            user_id=user.id,
            organization_id=membership.organization_id if membership else None,
        )
        metrics.record_auth(outcome="success")
        return await RefreshTokenService(self.db).issue_pair(user.id, user_agent=user_agent)

    async def me(self, user_id: UUID) -> UserOut:
        result = await self.db.execute(
            select(OrganizationMember)
            .options(selectinload(OrganizationMember.organization), selectinload(OrganizationMember.user))
            .where(OrganizationMember.user_id == user_id)
            .limit(1)
        )
        membership = result.scalar_one_or_none()
        if not membership:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Membership not found")
        from app.core.mode import effective_demo_mode

        demo = effective_demo_mode(membership.organization)
        return UserOut(
            id=membership.user.id,
            email=membership.user.email,
            full_name=membership.user.full_name,
            organization_id=membership.organization_id,
            organization_name=membership.organization.name,
            role=membership.role.value,
            demo_mode=demo,
            organization_demo_mode=bool(membership.organization.demo_mode),
            operating_mode="DEMO" if demo else "LIVE",
            env_demo_mode=bool(get_settings().demo_mode),
        )

    async def set_org_demo_mode(self, user_id: UUID, demo_mode: bool) -> UserOut:
        """Toggle organization demo flag. Env DEMO_MODE=true still forces DEMO operating mode."""
        result = await self.db.execute(
            select(OrganizationMember)
            .options(selectinload(OrganizationMember.organization), selectinload(OrganizationMember.user))
            .where(OrganizationMember.user_id == user_id)
            .limit(1)
        )
        membership = result.scalar_one_or_none()
        if not membership:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Membership not found")
        if membership.role.value not in {"owner", "admin"}:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin/owner required")
        membership.organization.demo_mode = demo_mode
        await self.db.flush()
        await write_audit(
            self.db,
            action="organization.demo_mode",
            organization_id=membership.organization_id,
            user_id=user_id,
            resource_type="organization",
            resource_id=str(membership.organization_id),
            details={"demo_mode": demo_mode},
        )
        return await self.me(user_id)
