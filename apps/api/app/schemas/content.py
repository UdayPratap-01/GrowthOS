from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


class ContentGenerateRequest(BaseModel):
    platform: str
    content_type: str
    objective: str
    audience: str | None = None
    tone: str | None = None
    topic: str
    cta: str | None = None


class ContentGenerated(BaseModel):
    hook: str
    main_copy: str
    cta: str
    visual_concept: str
    video_concept: str | None = None
    hashtags: list[str] = Field(default_factory=list)


class ContentSaveRequest(ContentGenerateRequest):
    hook: str
    main_copy: str
    cta: str
    visual_concept: str
    video_concept: str | None = None
    hashtags: list[str] = Field(default_factory=list)
    status: str = "draft"


class SocialPostOut(BaseModel):
    id: UUID
    client_id: UUID
    platform: str
    content_type: str
    hook: str | None
    main_copy: str | None
    cta: str | None
    visual_concept: str | None
    video_concept: str | None
    hashtags: list[str]
    status: str
    created_at: datetime

    model_config = {"from_attributes": True}


class CalendarCreate(BaseModel):
    title: str
    platform: str
    scheduled_for: datetime | None = None
    social_post_id: UUID | None = None
    notes: str | None = None
    status: str = "planned"


class CalendarOut(BaseModel):
    id: UUID
    client_id: UUID
    title: str
    platform: str
    scheduled_for: datetime | None
    social_post_id: UUID | None
    notes: str | None
    status: str
    created_at: datetime

    model_config = {"from_attributes": True}
