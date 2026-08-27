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


def reconciliation_blocks_retry(action) -> bool:
    """True when the action must not be claimed for re-execution."""
    recon = (action.result or {}).get("reconciliation") or {}
    return recon.get("state") in {
        ReconciliationState.pending.value,
        ReconciliationState.unknown.value,
    }
