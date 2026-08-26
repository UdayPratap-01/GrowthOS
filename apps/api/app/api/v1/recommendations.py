from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import AuthContext, get_current_auth
from app.core.permissions import Permission, require_permission
from app.db.session import get_db
from app.security.limits import ai_limit
from app.models.enums import RecommendationStatus
from app.schemas.recommendation import (
    RecommendationCreate,
    RecommendationGenerateRequest,
    RecommendationOut,
    RecommendationStatusUpdate,
)
from app.services.recommendation_service import RecommendationService

router = APIRouter(prefix="/recommendations", tags=["recommendations"])


@router.get("", response_model=list[RecommendationOut])
async def list_recommendations(
    client_id: UUID | None = None,
    status: RecommendationStatus | None = Query(default=None),
    auth: AuthContext = Depends(get_current_auth),
    db: AsyncSession = Depends(get_db),
) -> list[RecommendationOut]:
    return await RecommendationService(db).list(auth.organization_id, client_id=client_id, status_filter=status)


@router.post("", response_model=RecommendationOut, status_code=201)
async def create_recommendation(
    data: RecommendationCreate,
    auth: AuthContext = Depends(require_permission(Permission.content_write)),
    db: AsyncSession = Depends(get_db),
) -> RecommendationOut:
    return await RecommendationService(db).create(auth.organization_id, auth.user_id, data)


@router.post("/generate", response_model=list[RecommendationOut], dependencies=[Depends(ai_limit)])
async def generate_recommendations(
    data: RecommendationGenerateRequest | None = None,
    auth: AuthContext = Depends(require_permission(Permission.content_write)),
    db: AsyncSession = Depends(get_db),
) -> list[RecommendationOut]:
    client_id = data.client_id if data else None
    return await RecommendationService(db).generate_from_analytics(
        auth.organization_id, auth.user_id, client_id=client_id, demo_mode=auth.demo_mode
    )


@router.patch("/{recommendation_id}", response_model=RecommendationOut)
async def update_recommendation_status(
    recommendation_id: UUID,
    data: RecommendationStatusUpdate,
    auth: AuthContext = Depends(require_permission(Permission.content_write)),
    db: AsyncSession = Depends(get_db),
) -> RecommendationOut:
    return await RecommendationService(db).update_status(
        auth.organization_id, auth.user_id, recommendation_id, data
    )
