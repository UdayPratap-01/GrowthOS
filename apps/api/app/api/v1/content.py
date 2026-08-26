from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import AuthContext, get_current_auth
from app.core.permissions import Permission, require_permission
from app.db.session import get_db
from app.security.limits import ai_limit
from app.schemas.content import (
    CalendarCreate,
    CalendarOut,
    ContentGenerateRequest,
    ContentGenerated,
    ContentSaveRequest,
    SocialPostOut,
)
from app.security.quota import requires_quota
from app.services.content_service import ContentService
from app.services.usage_service import Metric

router = APIRouter(prefix="/clients/{client_id}/content", tags=["content"])


@router.post(
    "/generate",
    response_model=ContentGenerated,
    dependencies=[Depends(ai_limit), Depends(requires_quota(Metric.AI_REQUEST))],
)
async def generate_content(
    client_id: UUID,
    data: ContentGenerateRequest,
    auth: AuthContext = Depends(require_permission(Permission.content_write)),
    db: AsyncSession = Depends(get_db),
) -> ContentGenerated:
    return await ContentService(db).generate(auth.organization, client_id, data)


@router.post("/save", response_model=SocialPostOut, status_code=201)
async def save_content(
    client_id: UUID,
    data: ContentSaveRequest,
    auth: AuthContext = Depends(require_permission(Permission.content_write)),
    db: AsyncSession = Depends(get_db),
) -> SocialPostOut:
    return await ContentService(db).save(auth.organization_id, client_id, data)


@router.get("/posts", response_model=list[SocialPostOut])
async def list_posts(
    client_id: UUID,
    auth: AuthContext = Depends(get_current_auth),
    db: AsyncSession = Depends(get_db),
) -> list[SocialPostOut]:
    return await ContentService(db).list_posts(auth.organization_id, client_id)


@router.get("/calendar", response_model=list[CalendarOut])
async def list_calendar(
    client_id: UUID,
    auth: AuthContext = Depends(get_current_auth),
    db: AsyncSession = Depends(get_db),
) -> list[CalendarOut]:
    return await ContentService(db).list_calendar(auth.organization_id, client_id)


@router.post("/calendar", response_model=CalendarOut, status_code=201)
async def create_calendar(
    client_id: UUID,
    data: CalendarCreate,
    auth: AuthContext = Depends(require_permission(Permission.content_write)),
    db: AsyncSession = Depends(get_db),
) -> CalendarOut:
    return await ContentService(db).create_calendar_item(auth.organization_id, client_id, data)
