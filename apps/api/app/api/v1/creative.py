"""Creative media generation API — real files only."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.deps import AuthContext, get_current_auth
from app.core.permissions import Permission, require_permission
from app.core.mode import effective_demo_mode
from app.db.session import get_db
from app.security.limits import media_limit
from app.security.quota import requires_feature, requires_quota
from app.services.usage_service import Metric
from app.generation import get_image_provider, get_video_provider
from app.models.automation import CreativeAsset
from app.schemas.creative_media import (
    CreativeAssetMediaOut,
    ImageGenerateIn,
    MediaJobOut,
    ProviderStatusOut,
    VariationRequest,
    VideoGenerateIn,
)
from app.services.client_service import ClientService
from app.services.media_generation_service import MediaGenerationService
from app.storage import (
    StorageUnavailableError,
    get_object_storage,
    key_belongs_to_organization,
)

router = APIRouter(prefix="/creative", tags=["creative"])


def _job_out(result: dict) -> MediaJobOut:
    return MediaJobOut(
        job_id=result.get("job_id"),
        provider_job_id=result.get("provider_job_id"),
        status=result.get("status") or "FAILED",
        provider=result.get("provider"),
        prompt=result.get("prompt"),
        assets=result.get("assets") or [],
        error=result.get("error"),
        error_code=result.get("error_code"),
        retryable=bool(result.get("retryable")),
        message=result.get("message"),
        demo=bool(result.get("demo")),
        jobs=result.get("jobs") or [],
    )


@router.get("/providers", response_model=ProviderStatusOut)
async def provider_status(auth: AuthContext = Depends(get_current_auth)) -> ProviderStatusOut:
    settings = get_settings()
    img = get_image_provider()
    vid = get_video_provider()
    parts = []
    if not img.configured():
        parts.append("Image generation is not configured. Add IMAGE_PROVIDER and IMAGE_API_KEY.")
    if not vid.configured():
        parts.append("Video generation is not configured. Add VIDEO_PROVIDER and VIDEO_API_KEY.")
    return ProviderStatusOut(
        image_provider=img.name,
        image_configured=img.configured(),
        video_provider=vid.name,
        video_configured=vid.configured(),
        storage_backend=settings.storage_backend,
        demo_mode=effective_demo_mode(auth.organization),
        message=" ".join(parts) if parts else "Media providers ready.",
    )


@router.post(
    "/images/generate",
    response_model=MediaJobOut,
    dependencies=[Depends(media_limit), Depends(requires_quota(Metric.IMAGE_GENERATION))],
)
async def generate_images(
    data: ImageGenerateIn,
    auth: AuthContext = Depends(require_permission(Permission.content_write)),
    db: AsyncSession = Depends(get_db),
) -> MediaJobOut:
    await ClientService(db).get_client(auth.organization_id, data.client_id)
    result = await MediaGenerationService(db).enqueue_images(
        auth.organization,
        client_id=data.client_id,
        campaign_id=data.campaign_id,
        prompt=data.prompt,
        aspect_ratio=data.aspect_ratio,
        quantity=data.quantity,
        platform=data.platform,
        idempotency_key=data.idempotency_key,
    )
    return _job_out(result)


@router.get("/images/jobs/{job_id}", response_model=MediaJobOut)
async def image_job_status(
    job_id: UUID,
    auth: AuthContext = Depends(get_current_auth),
    db: AsyncSession = Depends(get_db),
) -> MediaJobOut:
    result = await MediaGenerationService(db).get_image_job(auth.organization_id, job_id)
    if result.get("error") == "NOT_FOUND":
        raise HTTPException(status_code=404, detail="JOB_NOT_FOUND")
    return _job_out(result)


@router.post(
    "/videos/generate",
    response_model=MediaJobOut,
    dependencies=[
        Depends(media_limit),
        Depends(requires_feature("video_generation")),
        Depends(requires_quota(Metric.VIDEO_GENERATION)),
    ],
)
async def generate_videos(
    data: VideoGenerateIn,
    auth: AuthContext = Depends(require_permission(Permission.content_write)),
    db: AsyncSession = Depends(get_db),
) -> MediaJobOut:
    await ClientService(db).get_client(auth.organization_id, data.client_id)
    result = await MediaGenerationService(db).enqueue_video(
        auth.organization,
        client_id=data.client_id,
        campaign_id=data.campaign_id,
        prompt=data.prompt,
        duration_seconds=data.duration_seconds,
        aspect_ratio=data.aspect_ratio,
        platform=data.platform,
        idempotency_key=data.idempotency_key,
    )
    return _job_out(result)


@router.get("/videos/jobs/{job_id}", response_model=MediaJobOut)
async def video_job_status(
    job_id: UUID,
    auth: AuthContext = Depends(get_current_auth),
    db: AsyncSession = Depends(get_db),
) -> MediaJobOut:
    result = await MediaGenerationService(db).get_video_job(auth.organization_id, job_id, poll=True)
    if result.get("error") == "NOT_FOUND":
        raise HTTPException(status_code=404, detail="JOB_NOT_FOUND")
    return _job_out(result)


@router.post("/{asset_id}/variations", dependencies=[Depends(media_limit)])
async def create_variations(
    asset_id: UUID,
    data: VariationRequest | None = None,
    auth: AuthContext = Depends(require_permission(Permission.content_write)),
    db: AsyncSession = Depends(get_db),
) -> dict:
    result = await MediaGenerationService(db).create_variations(
        auth.organization,
        asset_id=asset_id,
        count=(data.count if data else 3),
        user_id=auth.user.id,
    )
    if result.get("error") == "NOT_FOUND":
        raise HTTPException(status_code=404, detail="ASSET_NOT_FOUND")
    return result


@router.get("/assets", response_model=list[CreativeAssetMediaOut])
async def list_assets(
    client_id: UUID | None = Query(default=None),
    asset_type: str | None = Query(default=None),
    auth: AuthContext = Depends(get_current_auth),
    db: AsyncSession = Depends(get_db),
) -> list[CreativeAssetMediaOut]:
    stmt = (
        select(CreativeAsset)
        .where(CreativeAsset.organization_id == auth.organization_id)
        .order_by(CreativeAsset.created_at.desc())
        .limit(200)
    )
    if client_id:
        stmt = stmt.where(CreativeAsset.client_id == client_id)
    if asset_type:
        stmt = stmt.where(CreativeAsset.asset_type == asset_type)
    rows = (await db.execute(stmt)).scalars().all()
    out: list[CreativeAssetMediaOut] = []
    for r in rows:
        item = CreativeAssetMediaOut.model_validate(r)
        # A URL is only offered for assets whose key is inside this tenant's
        # prefix. Existence is confirmed on read rather than here: one HEAD per
        # row against S3 would make listing 200 assets 200 network round-trips.
        if r.status == "completed" and key_belongs_to_organization(r.storage_key, auth.organization_id):
            item.url = f"/api/v1/creative/media/{r.id}"
        out.append(item)
    return out


@router.get("/media/{asset_id}")
async def get_media_bytes(
    asset_id: UUID,
    auth: AuthContext = Depends(get_current_auth),
    db: AsyncSession = Depends(get_db),
) -> Response:
    asset = await db.scalar(
        select(CreativeAsset).where(
            CreativeAsset.id == asset_id,
            CreativeAsset.organization_id == auth.organization_id,
        )
    )
    if not asset or not asset.storage_key:
        raise HTTPException(status_code=404, detail="ASSET_NOT_FOUND")
    # The query is already tenant-scoped; this second check means a forged or
    # corrupted storage_key cannot be used to read another tenant's object.
    if not key_belongs_to_organization(asset.storage_key, auth.organization_id):
        raise HTTPException(status_code=404, detail="ASSET_NOT_FOUND")

    storage = get_object_storage()
    try:
        data = await storage.get_bytes(asset.storage_key)
    except StorageUnavailableError as exc:
        # An outage is not a missing file. Saying 404 here would tell the user
        # their asset is gone when it is merely unreachable.
        raise HTTPException(status_code=503, detail="STORAGE_UNAVAILABLE") from exc
    if not data:
        raise HTTPException(status_code=404, detail="FILE_NOT_FOUND")
    mime = asset.mime_type or "application/octet-stream"
    headers = {"Cache-Control": "private, max-age=3600"}
    if asset.data_source == "demo":
        headers["X-GrowthOS-Demo"] = "true"
    return Response(content=data, media_type=mime, headers=headers)
