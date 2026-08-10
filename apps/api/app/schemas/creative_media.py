from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


class ImageGenerateIn(BaseModel):
    client_id: UUID
    campaign_id: UUID | None = None
    prompt: str = Field(min_length=1, max_length=4000)
    aspect_ratio: str = "1:1"
    quantity: int = Field(default=1, ge=1, le=5)
    platform: str | None = None
    idempotency_key: str | None = None


class VideoGenerateIn(BaseModel):
    client_id: UUID
    campaign_id: UUID | None = None
    prompt: str = Field(min_length=1, max_length=4000)
    duration_seconds: int = Field(default=10, ge=2, le=60)
    aspect_ratio: str = "9:16"
    platform: str | None = None
    idempotency_key: str | None = None


class MediaAssetOut(BaseModel):
    id: str
    url: str
    mime_type: str
    width: int | None = None
    height: int | None = None
    duration_seconds: int | None = None
    demo: bool = False


class MediaJobOut(BaseModel):
    job_id: str | None = None
    provider_job_id: str | None = None
    status: str
    provider: str | None = None
    prompt: str | None = None
    assets: list[MediaAssetOut] = Field(default_factory=list)
    error: str | None = None
    error_code: str | None = None
    retryable: bool = False
    message: str | None = None
    demo: bool = False
    jobs: list[dict] = Field(default_factory=list)


class VariationRequest(BaseModel):
    count: int = Field(default=3, ge=1, le=5)


class CreativeAssetMediaOut(BaseModel):
    id: UUID
    client_id: UUID
    campaign_id: UUID | None
    name: str
    asset_type: str
    platform: str | None
    prompt: str | None
    provider: str | None
    model: str | None = None
    storage_key: str | None = None
    mime_type: str | None = None
    width: int | None = None
    height: int | None = None
    duration_seconds: int | None = None
    status: str
    content: dict
    meta: dict
    data_source: str
    url: str | None = None
    created_at: datetime

    model_config = {"from_attributes": True}


class ProviderStatusOut(BaseModel):
    image_provider: str
    image_configured: bool
    video_provider: str
    video_configured: bool
    storage_backend: str
    demo_mode: bool
    message: str
