"""Provider error classification for ads execution and reconciliation."""

from __future__ import annotations

from enum import Enum

# Confirmed failures — safe to retry via normal FAILED claim path.
INTEGRATION_NOT_CONNECTED = "INTEGRATION_NOT_CONNECTED"
CREDENTIALS_EXPIRED = "CREDENTIALS_EXPIRED"
NOT_CONFIGURED = "NOT_CONFIGURED"
UNSUPPORTED_OPERATION = "UNSUPPORTED_OPERATION"
EXTERNAL_ID_REQUIRED = "EXTERNAL_ID_REQUIRED"
TARGET_NOT_FOUND = "TARGET_NOT_FOUND"
BUDGET_REQUIRED = "BUDGET_REQUIRED"
PLATFORM_API_ERROR = "PLATFORM_API_ERROR"
EXECUTION_NOT_CONFIRMED = "EXECUTION_NOT_CONFIRMED"
RATE_LIMITED = "RATE_LIMITED"

# Ambiguous — mutation outcome unknown; must not auto re-execute.
PROVIDER_TIMEOUT_AMBIGUOUS = "PROVIDER_TIMEOUT_AMBIGUOUS"
PROVIDER_TRANSPORT_AMBIGUOUS = "PROVIDER_TRANSPORT_AMBIGUOUS"

CONFIRMED_FAILURE_CODES = frozenset(
    {
        INTEGRATION_NOT_CONNECTED,
        CREDENTIALS_EXPIRED,
        NOT_CONFIGURED,
        UNSUPPORTED_OPERATION,
        EXTERNAL_ID_REQUIRED,
        TARGET_NOT_FOUND,
        BUDGET_REQUIRED,
        EXECUTION_NOT_CONFIRMED,
        RATE_LIMITED,
        "AUTHORIZATION_ERROR",
        "VALIDATION_ERROR",
        "CONFLICT",
        PLATFORM_API_ERROR,
    }
)


class ReconciliationState(str, Enum):
    pending = "PENDING"
    unknown = "UNKNOWN"
    confirmed_success = "CONFIRMED_SUCCESS"
    confirmed_not_applied = "CONFIRMED_NOT_APPLIED"


class ReconciliationOutcome(str, Enum):
    confirmed_success = "CONFIRMED_SUCCESS"
    confirmed_not_applied = "CONFIRMED_NOT_APPLIED"
    unknown = "UNKNOWN"
    unsupported = "UNSUPPORTED"


class VerificationErrorCategory(str, Enum):
    """Normalized categories for provider preflight / read-only verification."""

    authentication = "AUTHENTICATION"
    authorization = "AUTHORIZATION"
    configuration = "CONFIGURATION"
    account_not_found = "ACCOUNT_NOT_FOUND"
    account_access = "ACCOUNT_ACCESS"
    rate_limit = "RATE_LIMIT"
    timeout = "TIMEOUT"
    network = "NETWORK"
    provider_unavailable = "PROVIDER_UNAVAILABLE"
    api_error = "API_ERROR"
    unknown = "UNKNOWN"


def is_ambiguous_error_code(code: str | None) -> bool:
    return code in {PROVIDER_TIMEOUT_AMBIGUOUS, PROVIDER_TRANSPORT_AMBIGUOUS}


def is_confirmed_failure_code(code: str | None) -> bool:
    if not code:
        return False
    if is_ambiguous_error_code(code):
        return False
    if code.startswith("HTTP_"):
        return True
    return code in CONFIRMED_FAILURE_CODES


def classify_meta_graph_error(
    *,
    status_code: int,
    body: dict | None = None,
    text: str = "",
) -> tuple[str, str]:
    """
    Map Meta Graph API errors to normalized (error_code, category).

    Returns (AdsExecutor error_code, VerificationErrorCategory value).
    Never echoes tokens from provider payloads.
    """
    err = (body or {}).get("error") if isinstance(body, dict) else None
    err = err if isinstance(err, dict) else {}
    code = err.get("code")
    subcode = err.get("error_subcode")
    msg = str(err.get("message") or text or "")[:240].lower()

    if status_code == 429 or code == 4 or code == 17 or code == 32 or "rate limit" in msg:
        return RATE_LIMITED, VerificationErrorCategory.rate_limit.value
    if status_code == 401 or code in {190, 102} or "session has expired" in msg or "invalid oauth" in msg:
        return CREDENTIALS_EXPIRED, VerificationErrorCategory.authentication.value
    if status_code == 403 or code in {10, 200, 294} or "permission" in msg or "insufficient" in msg:
        return "AUTHORIZATION_ERROR", VerificationErrorCategory.authorization.value
    if status_code == 404 or code == 803 or "does not exist" in msg or "unsupported get request" in msg:
        return TARGET_NOT_FOUND, VerificationErrorCategory.account_not_found.value
    if status_code == 400 and ("validation" in msg or "invalid parameter" in msg or code == 100):
        return "VALIDATION_ERROR", VerificationErrorCategory.api_error.value
    if status_code == 409 or "conflict" in msg:
        return "CONFLICT", VerificationErrorCategory.api_error.value
    if status_code >= 500:
        return PLATFORM_API_ERROR, VerificationErrorCategory.provider_unavailable.value
    if status_code >= 400:
        return f"HTTP_{status_code}", VerificationErrorCategory.api_error.value
    return PLATFORM_API_ERROR, VerificationErrorCategory.unknown.value


def classify_google_ads_error(
    *,
    status_code: int,
    body: dict | None = None,
    text: str = "",
) -> tuple[str, str]:
    """
    Map Google Ads API errors to normalized (error_code, category).

    Never echoes access tokens or developer tokens from provider payloads.
    """
    err = (body or {}).get("error") if isinstance(body, dict) else None
    err = err if isinstance(err, dict) else {}
    status_obj = err.get("status") if isinstance(err.get("status"), str) else ""
    msg = str(err.get("message") or text or "")[:240].lower()
    details = err.get("details") if isinstance(err.get("details"), list) else []
    detail_blob = " ".join(str(d) for d in details)[:400].lower()
    combined = f"{msg} {detail_blob} {status_obj.lower()}"

    if status_code == 429 or "resource_exhausted" in combined or "rate limit" in combined or "quota" in combined:
        return RATE_LIMITED, VerificationErrorCategory.rate_limit.value
    if (
        status_code == 401
        or "unauthenticated" in combined
        or "invalid_grant" in combined
        or "access token" in combined
        or "expired" in combined
    ):
        return CREDENTIALS_EXPIRED, VerificationErrorCategory.authentication.value
    if (
        status_code == 403
        or "permission_denied" in combined
        or "permission" in combined
        or "developer token" in combined
        or "authorization" in combined
    ):
        return "AUTHORIZATION_ERROR", VerificationErrorCategory.authorization.value
    if status_code == 404 or "not_found" in combined or "does not exist" in combined:
        return TARGET_NOT_FOUND, VerificationErrorCategory.account_not_found.value
    if status_code == 400 and (
        "invalid_argument" in combined or "validation" in combined or "invalid" in combined
    ):
        return "VALIDATION_ERROR", VerificationErrorCategory.api_error.value
    if status_code == 409 or "already_exists" in combined or "conflict" in combined:
        return "CONFLICT", VerificationErrorCategory.api_error.value
    if status_code >= 500 or "unavailable" in combined or "internal" in combined:
        return PLATFORM_API_ERROR, VerificationErrorCategory.provider_unavailable.value
    if status_code >= 400:
        return f"HTTP_{status_code}", VerificationErrorCategory.api_error.value
    return PLATFORM_API_ERROR, VerificationErrorCategory.unknown.value


def reconciliation_blocks_retry(action) -> bool:
    """True when the action must not be claimed for re-execution."""
    recon = (action.result or {}).get("reconciliation") or {}
    return recon.get("state") in {
        ReconciliationState.pending.value,
        ReconciliationState.unknown.value,
    }
