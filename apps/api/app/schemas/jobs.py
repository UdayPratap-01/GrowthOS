from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class JobOut(BaseModel):
    """
    A background job as the frontend sees it.

    `status` is one of QUEUED | RUNNING | RETRYING | COMPLETED | FAILED |
    CANCELLED (plus the media-specific intermediate states). `terminal` tells a
    polling client when to stop.
    """

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    job_type: str
    status: str
    attempts: int
    max_attempts: int
    error: str | None = None
    result: dict = {}
    run_after: datetime | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    created_at: datetime | None = None
    terminal: bool = False


class JobAcceptedOut(BaseModel):
    """Returned by endpoints that hand work to a worker rather than doing it."""

    job_id: UUID
    status: str = "QUEUED"
    poll_url: str
    message: str
