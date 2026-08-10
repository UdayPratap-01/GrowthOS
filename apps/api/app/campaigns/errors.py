"""
Failure codes for the campaign engine.

These subclass `AppError`, so the global handler in `app/core/errors.py` renders
them in the standard envelope with a stable `code` the frontend branches on. No
provider name, key, prompt or traceback is ever placed in `message` — the detail
that would help an attacker or leak a secret goes to the log instead.

The distinction that matters here is between the three ways a generation can not
happen: the request was wrong (400), the plan does not cover it (402), or a
provider is absent (503). Collapsing them into one error would leave the UI
unable to say what the user should do about it.
"""

from __future__ import annotations

from starlette.status import (
    HTTP_400_BAD_REQUEST,
    HTTP_402_PAYMENT_REQUIRED,
    HTTP_409_CONFLICT,
    HTTP_502_BAD_GATEWAY,
    HTTP_503_SERVICE_UNAVAILABLE,
)

from app.core.errors import AppError


class InvalidCampaignRequest(AppError):
    """The request could not be satisfied as written. The caller must change it."""

    status_code = HTTP_400_BAD_REQUEST
    code = "INVALID_CAMPAIGN_REQUEST"


class CampaignGenerationFailed(AppError):
    status_code = HTTP_502_BAD_GATEWAY
    code = "CAMPAIGN_GENERATION_FAILED"


class MediaProviderNotConfigured(AppError):
    """
    No image or video provider is configured.

    503 rather than 500: the service is fine, a capability is absent, and the fix
    is configuration. Raised only where the caller asked for media and nothing
    else can be done; a campaign generation that merely *included* media records
    the stage as NOT_CONFIGURED and continues.
    """

    status_code = HTTP_503_SERVICE_UNAVAILABLE
    code = "MEDIA_PROVIDER_NOT_CONFIGURED"


class UsageLimitReached(AppError):
    status_code = HTTP_402_PAYMENT_REQUIRED
    code = "USAGE_LIMIT_REACHED"


class CampaignStateConflict(AppError):
    """
    The campaign is not in a state where this action makes sense — approving a
    run that is still generating, or rejecting one already approved.
    """

    status_code = HTTP_409_CONFLICT
    code = "INVALID_CAMPAIGN_STATE"
