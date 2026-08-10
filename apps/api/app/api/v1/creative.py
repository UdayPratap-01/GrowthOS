"""Creative media generation API — real files only."""

from __future__ import annotations

from datetime import datetime, timezone
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


@router.post("/images/jobs/{job_id}/cancel", response_model=MediaJobOut)
async def cancel_image_job(
    job_id: UUID,
    auth: AuthContext = Depends(require_permission(Permission.content_write)),
    db: AsyncSession = Depends(get_db),
) -> MediaJobOut:
    result = await MediaGenerationService(db).cancel_job(auth.organization_id, job_id, kind="image")
    if result.get("error") == "NOT_FOUND":
        raise HTTPException(status_code=404, detail="JOB_NOT_FOUND")
    return _job_out(result)


@router.post("/videos/jobs/{job_id}/cancel", response_model=MediaJobOut)
async def cancel_video_job(
    job_id: UUID,
    auth: AuthContext = Depends(require_permission(Permission.content_write)),
    db: AsyncSession = Depends(get_db),
) -> MediaJobOut:
    """
    Stop a video generation that is no longer wanted.

    The provider is asked to cancel first, so a long generation stops costing
    money rather than merely being hidden from the library.
    """
    result = await MediaGenerationService(db).cancel_job(auth.organization_id, job_id, kind="video")
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
    campaign_id: UUID | None = Query(default=None),
    concept_id: UUID | None = Query(default=None),
    variation_id: UUID | None = Query(default=None),
    asset_type: str | None = Query(default=None),
    status_filter: str | None = Query(default=None, alias="status"),
    include_archived: bool = Query(default=False),
    limit: int = Query(default=200, ge=1, le=500),
    auth: AuthContext = Depends(get_current_auth),
    db: AsyncSession = Depends(get_db),
) -> list[CreativeAssetMediaOut]:
    """
    The creative library. Every filter is applied inside the tenant scope.

    Archived assets are excluded by default rather than deleted: a reviewer who
    archived the wrong render should be able to get it back, and a hard delete
    would also orphan any ad that references it.
    """
    stmt = (
        select(CreativeAsset)
        .where(CreativeAsset.organization_id == auth.organization_id)
        .order_by(CreativeAsset.created_at.desc())
        .limit(limit)
    )
    if client_id:
        stmt = stmt.where(CreativeAsset.client_id == client_id)
    if campaign_id:
        stmt = stmt.where(CreativeAsset.campaign_id == campaign_id)
    if concept_id:
        stmt = stmt.where(CreativeAsset.concept_id == concept_id)
    if variation_id:
        stmt = stmt.where(CreativeAsset.variation_id == variation_id)
    if asset_type:
        stmt = stmt.where(CreativeAsset.asset_type == asset_type)
    if status_filter:
        stmt = stmt.where(CreativeAsset.status == status_filter.lower())
    if not include_archived:
        stmt = stmt.where(CreativeAsset.archived_at.is_(None))
    rows = (await db.execute(stmt)).scalars().all()
    out: list[CreativeAssetMediaOut] = []
    for r in rows:
        item = CreativeAssetMediaOut.model_validate(r)
        item.is_real = r.data_source != "demo"
        # A URL is only offered for assets whose key is inside this tenant's
        # prefix. Existence is confirmed on read rather than here: one HEAD per
        # row against S3 would make listing 200 assets 200 network round-trips.
        if r.status == "completed" and key_belongs_to_organization(r.storage_key, auth.organization_id):
            item.url = f"/api/v1/creative/media/{r.id}"
        out.append(item)
    return out


@router.post("/assets/{asset_id}/archive", response_model=CreativeAssetMediaOut)
async def archive_asset(
    asset_id: UUID,
    archived: bool = Query(default=True),
    auth: AuthContext = Depends(require_permission(Permission.content_write)),
    db: AsyncSession = Depends(get_db),
) -> CreativeAssetMediaOut:
    """Hide an asset from the library, or restore it. The file is not deleted."""
    asset = await db.scalar(
        select(CreativeAsset).where(
            CreativeAsset.id == asset_id,
            CreativeAsset.organization_id == auth.organization_id,
        )
    )
    if not asset:
        raise HTTPException(status_code=404, detail="ASSET_NOT_FOUND")
    asset.archived_at = datetime.now(timezone.utc) if archived else None
    await db.flush()
    item = CreativeAssetMediaOut.model_validate(asset)
    item.is_real = asset.data_source != "demo"
    if asset.status == "completed" and key_belongs_to_organization(
        asset.storage_key, auth.organization_id
    ):
        item.url = f"/api/v1/creative/media/{asset.id}"
    return item


@router.get("/media/{asset_id}")
async def get_media_bytes(
    asset_id: UUID,
    download: bool = Query(default=False),
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
    if download:
        # Served through the same authenticated, tenant-checked route rather than
        # a public link, so "download when authorized" stays true.
        headers["Content-Disposition"] = f'attachment; filename="{_download_name(asset, mime)}"'
    return Response(content=data, media_type=mime, headers=headers)


def _download_name(asset: CreativeAsset, mime: str) -> str:
    extension = {"image/png": "png", "image/jpeg": "jpg", "video/mp4": "mp4"}.get(mime, "bin")
    # Only characters that are safe in a header and on a filesystem: the name
    # originates in a model-written prompt.
    safe = "".join(ch if ch.isalnum() or ch in "-_" else "-" for ch in (asset.name or "asset"))
    return f"{safe[:60].strip('-') or 'asset'}-{str(asset.id)[:8]}.{extension}"
