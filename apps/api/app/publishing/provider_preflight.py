"""Local provider preflight — configuration / connection checks only (no network)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.integrations.persistence import get_integration_row, load_tokens


class PreflightStatus(str, Enum):
    configured = "CONFIGURED"
    not_configured = "NOT_CONFIGURED"
    partially_configured = "PARTIALLY_CONFIGURED"
    invalid_configuration = "INVALID_CONFIGURATION"
    connected = "CONNECTED"
    not_connected = "NOT_CONNECTED"
    demo = "DEMO"
    blocked = "BLOCKED"


class CheckStatus(str, Enum):
    pass_ = "PASS"
    fail = "FAIL"
    skipped = "SKIPPED"


@dataclass
class PreflightCheck:
    name: str
    status: CheckStatus
    reason: str

    def as_dict(self) -> dict[str, Any]:
        return {"name": self.name, "status": self.status.value, "reason": self.reason}


@dataclass
class PreflightResult:
    provider: str
    status: PreflightStatus
    checks: list[PreflightCheck] = field(default_factory=list)
    credentials_configured: bool = False
    integration_connected: bool = False
    account_hint: str | None = None
    demo_mode: bool = False
    checked_at: str = ""
    safe_for_read: bool = False
    safe_for_mutation: bool = False  # Phase 1 always false

    def as_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "status": self.status.value,
            "checks": [c.as_dict() for c in self.checks],
            "credentials_configured": self.credentials_configured,
            "integration_connected": self.integration_connected,
            "account_hint": self.account_hint,
            "demo_mode": self.demo_mode,
            "checked_at": self.checked_at,
            "safe_for_read": self.safe_for_read,
            "safe_for_mutation": False,
        }


def _normalize_provider(provider: str) -> str:
    p = (provider or "").strip().lower()
    if p in {"meta", "facebook"}:
        return "meta"
    if p in {"google", "google_ads"}:
        return "google_ads"
    return p


async def run_provider_preflight(
    db: AsyncSession,
    *,
    organization_id: UUID,
    provider: str,
    client_id: UUID | None = None,
    settings: Settings | None = None,
) -> PreflightResult:
    """
    Evaluate local configuration + integration row. Does not call Meta/Google.
    """
    settings = settings or get_settings()
    provider = _normalize_provider(provider)
    now = datetime.now(timezone.utc).isoformat()
    checks: list[PreflightCheck] = []

    if provider not in {"meta", "google_ads"}:
        return PreflightResult(
            provider=provider,
            status=PreflightStatus.blocked,
            checks=[PreflightCheck("provider", CheckStatus.fail, f"Unsupported provider {provider}")],
            checked_at=now,
            demo_mode=bool(settings.demo_mode),
        )

    if provider == "meta":
        app_id = bool((settings.meta_app_id or "").strip())
        app_secret = bool((settings.meta_app_secret or "").strip())
        checks.append(
            PreflightCheck(
                "meta_app_id",
                CheckStatus.pass_ if app_id else CheckStatus.fail,
                "META_APP_ID set" if app_id else "META_APP_ID missing",
            )
        )
        checks.append(
            PreflightCheck(
                "meta_app_secret",
                CheckStatus.pass_ if app_secret else CheckStatus.fail,
                "META_APP_SECRET set" if app_secret else "META_APP_SECRET missing",
            )
        )
        credentials_configured = app_id and app_secret
        if app_id != app_secret and (app_id or app_secret):
            # one of the pair missing
            status = PreflightStatus.partially_configured
        elif not credentials_configured:
            status = PreflightStatus.not_configured
        else:
            status = PreflightStatus.configured
        integration_provider = "meta"
    else:
        cid = bool((settings.google_client_id or "").strip())
        csec = bool((settings.google_client_secret or "").strip())
        dtok = bool((settings.google_ads_developer_token or "").strip())
        checks.append(
            PreflightCheck(
                "google_client_id",
                CheckStatus.pass_ if cid else CheckStatus.fail,
                "GOOGLE_CLIENT_ID set" if cid else "GOOGLE_CLIENT_ID missing",
            )
        )
        checks.append(
            PreflightCheck(
                "google_client_secret",
                CheckStatus.pass_ if csec else CheckStatus.fail,
                "GOOGLE_CLIENT_SECRET set" if csec else "GOOGLE_CLIENT_SECRET missing",
            )
        )
        checks.append(
            PreflightCheck(
                "google_ads_developer_token",
                CheckStatus.pass_ if dtok else CheckStatus.fail,
                "GOOGLE_ADS_DEVELOPER_TOKEN set" if dtok else "GOOGLE_ADS_DEVELOPER_TOKEN missing",
            )
        )
        login = (settings.google_ads_login_customer_id or "").strip()
        checks.append(
            PreflightCheck(
                "google_ads_login_customer_id",
                CheckStatus.pass_ if login else CheckStatus.skipped,
                f"optional MCC set" if login else "optional; not set",
            )
        )
        n_present = sum([cid, csec, dtok])
        credentials_configured = n_present == 3
        if n_present == 0:
            status = PreflightStatus.not_configured
        elif n_present < 3:
            status = PreflightStatus.partially_configured
        else:
            status = PreflightStatus.configured
        integration_provider = "google_ads"

    row = await get_integration_row(
        db, organization_id=organization_id, provider=integration_provider, client_id=client_id
    )
    if (not row or not row.secret_ref) and client_id is not None:
        row = await get_integration_row(
            db, organization_id=organization_id, provider=integration_provider, client_id=None
        )

    tokens = load_tokens(row) if row else None
    has_token = bool(tokens and (tokens.get("access_token") or tokens.get("refresh_token")))
    connected = bool(row and row.secret_ref and (row.status or "").lower() in {"connected", "active", ""})
    if connected and has_token:
        checks.append(PreflightCheck("oauth_tokens", CheckStatus.pass_, "Encrypted OAuth tokens present"))
    elif row and row.secret_ref and not has_token:
        checks.append(
            PreflightCheck("oauth_tokens", CheckStatus.fail, "secret_ref present but tokens unreadable")
        )
        status = PreflightStatus.invalid_configuration
    else:
        checks.append(PreflightCheck("oauth_tokens", CheckStatus.fail, "Integration not connected"))

    account_hint = None
    if row and row.config:
        account_hint = row.config.get("account_label") or row.config.get("external_account_id")
        if account_hint:
            checks.append(PreflightCheck("account_hint", CheckStatus.pass_, "Account label present in config"))
        else:
            checks.append(PreflightCheck("account_hint", CheckStatus.skipped, "No account label yet"))

    demo = bool(settings.demo_mode)
    if demo and not connected and status in {PreflightStatus.not_configured, PreflightStatus.configured}:
        # Demo does not become VERIFIED / CONNECTED
        if status == PreflightStatus.not_configured:
            final = PreflightStatus.demo
        else:
            final = status
    elif connected and credentials_configured and status == PreflightStatus.configured:
        final = PreflightStatus.connected
    else:
        final = status

    # Local preflight never proves read access — only that we *might* be able to verify.
    safe_for_read = False

    return PreflightResult(
        provider=integration_provider,
        status=final,
        checks=checks,
        credentials_configured=credentials_configured,
        integration_connected=bool(connected and has_token),
        account_hint=str(account_hint) if account_hint else None,
        demo_mode=demo,
        checked_at=now,
        safe_for_read=safe_for_read,
        safe_for_mutation=False,
    )
