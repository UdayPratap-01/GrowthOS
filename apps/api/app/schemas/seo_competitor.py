from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, HttpUrl


class SeoCompetitorCreate(BaseModel):
    root_url: HttpUrl | str
    display_name: str | None = Field(default=None, max_length=255)


class SeoCompetitorOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    organization_id: UUID
    display_name: str | None
    root_url: str
    domain: str
    status: str
    created_at: datetime | None = None


class SeoCompetitorCrawlRequest(BaseModel):
    max_pages: int | None = Field(default=None, ge=1, le=100)


class SeoCompetitorCrawlOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    competitor_id: UUID
    root_url: str
    status: str
    config: dict
    stats: dict
    error: str | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None


class SeoCompetitorPageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    url: str
    title: str | None
    h1: str | None
    headings: list
    topic_label: str | None
    http_status: int | None
    fetched_at: datetime | None = None
