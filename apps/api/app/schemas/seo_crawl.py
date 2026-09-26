from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, HttpUrl


class SeoCrawlCreateRequest(BaseModel):
    root_url: HttpUrl
    client_id: UUID | None = None
    max_pages: int | None = Field(default=None, ge=1, le=500)
    max_depth: int | None = Field(default=None, ge=0, le=10)
    request_timeout: float | None = Field(default=None, ge=1.0, le=30.0)
    include_subdomains: bool = False


class SeoCrawlOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    root_url: str
    status: str
    config: dict = {}
    stats: dict = {}
    error: str | None = None
    cancel_requested: bool = False
    job_id: UUID | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    created_at: datetime | None = None


class SeoCrawlPageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    url: str
    final_url: str | None = None
    depth: int
    referrer_url: str | None = None
    http_status: int | None = None
    content_type: str | None = None
    response_bytes: int
    redirect_count: int
    response_time_ms: float | None = None
    error_code: str | None = None
    observations: dict = {}
    data_source: str
