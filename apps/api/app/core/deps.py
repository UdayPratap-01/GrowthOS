from dataclasses import dataclass
from uuid import UUID

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.security import safe_decode_token
from app.db.session import get_db
from app.models.organization import Organization, OrganizationMember
from app.models.user import User
from app.observability.logging import bind_request_context
from app.security.rate_limit import rate_limit_dependency

bearer = HTTPBearer(auto_error=False)


@dataclass
class AuthContext:
    user: User
    organization: Organization
    membership: OrganizationMember

    @property
    def organization_id(self) -> UUID:
        return self.organization.id

    @property
    def user_id(self) -> UUID:
        return self.user.id

    @property
    def demo_mode(self) -> bool:
        from app.core.mode import effective_demo_mode

        return effective_demo_mode(self.organization)


async def get_current_auth(
    creds: HTTPAuthorizationCredentials | None = Depends(bearer),
    db: AsyncSession = Depends(get_db),
    _: None = Depends(rate_limit_dependency),
) -> AuthContext:
    if creds is None or creds.scheme.lower() != "bearer":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
    payload = safe_decode_token(creds.credentials)
    if not payload or payload.get("type") != "access":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")
    try:
        user_id = UUID(payload["sub"])
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token subject") from exc

    user = await db.get(User, user_id)
    if not user or not user.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User inactive or missing")

    result = await db.execute(
        select(OrganizationMember)
        .options(selectinload(OrganizationMember.organization))
        .where(OrganizationMember.user_id == user.id)
        .limit(1)
    )
    membership = result.scalar_one_or_none()
    if not membership:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="No organization membership")

    # Bind tenant identity so every subsequent log line in this request is
    # attributable without threading the context through call signatures.
    bind_request_context(
        organization_id=str(membership.organization_id), user_id=str(user.id)
    )
    return AuthContext(user=user, organization=membership.organization, membership=membership)
