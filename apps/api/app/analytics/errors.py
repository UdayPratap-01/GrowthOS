"""Typed ingestion failures — distinguish retryable transport from permanent config errors."""

from __future__ import annotations


class AnalyticsIngestionError(Exception):
    """Base class for analytics ingestion failures."""

    code: str = "INGESTION_FAILED"
    retryable: bool = False

    def __init__(self, message: str, *, code: str | None = None, retryable: bool | None = None) -> None:
        super().__init__(message)
        if code is not None:
            self.code = code
        if retryable is not None:
            self.retryable = retryable
        self.message = message


class CredentialsMissing(AnalyticsIngestionError):
    code = "CREDENTIALS_MISSING"
    retryable = False


class IntegrationDisconnected(AnalyticsIngestionError):
    code = "INTEGRATION_DISCONNECTED"
    retryable = False


class CredentialsExpired(AnalyticsIngestionError):
    code = "CREDENTIALS_EXPIRED"
    retryable = False


class ProviderRateLimited(AnalyticsIngestionError):
    code = "RATE_LIMITED"
    retryable = True


class ProviderTimeout(AnalyticsIngestionError):
    code = "PROVIDER_TIMEOUT"
    retryable = True


class ProviderTransportError(AnalyticsIngestionError):
    code = "PROVIDER_TRANSPORT_ERROR"
    retryable = True


class UnsupportedProvider(AnalyticsIngestionError):
    code = "UNSUPPORTED_PROVIDER"
    retryable = False


class UnsupportedOperation(AnalyticsIngestionError):
    code = "UNSUPPORTED_OPERATION"
    retryable = False


class MalformedProviderResponse(AnalyticsIngestionError):
    code = "MALFORMED_PROVIDER_RESPONSE"
    retryable = False


class IngestionDisabled(AnalyticsIngestionError):
    code = "INGESTION_DISABLED"
    retryable = False


class ClientRequired(AnalyticsIngestionError):
    code = "CLIENT_REQUIRED"
    retryable = False
