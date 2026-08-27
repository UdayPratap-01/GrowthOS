"""Read-only Meta / Google Ads provider verification (Milestone 5 Phase 1).

Never mutates ads. Never calls ActionService / creates AIAction / enqueues execution.
Never logs or returns secrets.
"""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Protocol
from uuid import UUID

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.automation.idempotency import sanitize_platform_response
from app.core.config import Settings, get_settings
from app.integrations.google_oauth import ensure_access_token
from app.integrations.meta_family import META_GRAPH
from app.integrations.persistence import get_integration_row, load_tokens
from app.observability import events, metrics
from app.publishing.provider_errors import VerificationErrorCategory
from app.publishing.provider_preflight import (
    CheckStatus,
    PreflightResult,
    PreflightStatus,
    run_provider_preflight,
)
from app.security.audit import write_audit

# M4 mutation harness confirm (still unused for live mutations in Phase 1).
CONFIRM_PHRASE = "I_CONFIRM_LIVE_MUTATIONS"
# Phase 1 read-only operator / CLI confirmation.
READ_ONLY_CONFIRM_PHRASE = "I_CONFIRM_READ_ONLY_PROVIDER_VERIFICATION"

META_GRAPH_VERSIONED = META_GRAPH
GOOGLE_ADS_API = "https://googleads.googleapis.com/v18"


class AsyncHttpClient(Protocol):
    async def get(self, url: str, *, params: dict | None = None, headers: dict | None = None, **kwargs): ...

    async def post(self, url: str, *, json: dict | None = None, data: Any = None, headers: dict | None = None, **kwargs): ...


@dataclass
class VerificationStepResult:
    step: str
    ok: bool
    detail: str
    observed: dict[str, Any] = field(default_factory=dict)
    category: str | None = None

    def as_dict(self) -> dict[str, Any]:
        body = {
            "step": self.step,
            "ok": self.ok,
            "detail": self.detail,
            "observed": sanitize_platform_response(self.observed),
            "status": "PASS" if self.ok else "FAIL",
        }
        if self.category:
            body["category"] = self.category
        return body


@dataclass
class VerificationReport:
    """Backward-compatible M4 CLI report shape + Phase 1 fields."""

    provider: str
    ran: bool
    skipped_reason: str | None = None
    steps: list[VerificationStepResult] = field(default_factory=list)
    status: str = "NOT_CONFIGURED"
    account: dict[str, Any] = field(default_factory=dict)
    authentication: dict[str, Any] = field(default_factory=dict)
    authorization: dict[str, Any] = field(default_factory=dict)
    capabilities: list[dict[str, Any]] = field(default_factory=list)
    checks: list[dict[str, Any]] = field(default_factory=list)
    # Sanitized account + campaign discovery for canary configuration (no secrets).
    canary_resources: dict[str, Any] = field(default_factory=dict)
    safe_for_read: bool = False
    safe_for_mutation: bool = False
    checked_at: str = ""
    environment: str = ""
    error_category: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "status": self.status,
            "ran": self.ran,
            "skipped_reason": self.skipped_reason,
            "steps": [s.as_dict() for s in self.steps],
            "checks": self.checks or [s.as_dict() for s in self.steps],
            "account": sanitize_platform_response(self.account),
            "authentication": self.authentication,
            "authorization": self.authorization,
            "capabilities": self.capabilities,
            "canary_resources": sanitize_platform_response(self.canary_resources),
            "safe_for_read": self.safe_for_read,
            "safe_for_mutation": False,  # Phase 1 hard rule
            "checked_at": self.checked_at,
            "environment": self.environment,
            "error_category": self.error_category,
            "live_provider_verification": "RAN" if self.ran else "NOT_RUN",
            "phase": "read_only",
        }


def verification_preflight(settings: Settings | None = None) -> tuple[bool, str | None]:
    """M4 CLI gate for opt-in live tooling (mutation path still blocked in Phase 1)."""
    settings = settings or get_settings()
    if not settings.provider_verification_enabled:
        return False, "PROVIDER_VERIFICATION_ENABLED=false"
    if (settings.provider_verification_confirm or "").strip() != CONFIRM_PHRASE:
        return False, f"PROVIDER_VERIFICATION_CONFIRM must equal {CONFIRM_PHRASE!r}"
    if not (settings.provider_verification_org_id or "").strip():
        return False, "PROVIDER_VERIFICATION_ORG_ID required"
    if not (settings.provider_verification_client_id or "").strip():
        return False, "PROVIDER_VERIFICATION_CLIENT_ID required"
    return True, None


def require_read_only_confirm(confirm: str | None) -> tuple[bool, str | None]:
    if (confirm or "").strip() != READ_ONLY_CONFIRM_PHRASE:
        return False, f"confirm must equal {READ_ONLY_CONFIRM_PHRASE!r}"
    return True, None


def _hash_id(value: str | None) -> str | None:
    if not value:
        return None
    return hashlib.sha256(str(value).encode()).hexdigest()[:16]


def _classify_http_error(status_code: int, body: str) -> VerificationErrorCategory:
    text = (body or "").lower()
    if status_code == 401:
        return VerificationErrorCategory.authentication
    if status_code == 403:
        return VerificationErrorCategory.authorization
    if status_code == 404:
        return VerificationErrorCategory.account_not_found
    if status_code == 429:
        return VerificationErrorCategory.rate_limit
    if status_code >= 500:
        return VerificationErrorCategory.provider_unavailable
    if "invalid_grant" in text:
        return VerificationErrorCategory.authentication
    if "developer token" in text:
        return VerificationErrorCategory.authorization
    if "customer" in text and ("not found" in text or "permission" in text):
        return VerificationErrorCategory.account_access
    if "permission" in text or "scope" in text:
        return VerificationErrorCategory.authorization
    return VerificationErrorCategory.api_error


def _safe_error_detail(exc: BaseException) -> str:
    """Never echo tokens that might appear in provider error bodies."""
    text = str(exc)
    for needle in ("access_token", "refresh_token", "client_secret", "developer-token", "Bearer "):
        if needle.lower() in text.lower():
            return type(exc).__name__
    # Truncate raw bodies
    return text[:240]


async def verify_provider_readonly(
    db: AsyncSession,
    *,
    organization_id: UUID,
    provider: str,
    client_id: UUID | None = None,
    confirm: str | None = None,
    actor_user_id: UUID | None = None,
    http_client: AsyncHttpClient | None = None,
    settings: Settings | None = None,
) -> VerificationReport:
    """
    Full Phase 1 verification: preflight + optional live READ-ONLY API checks.

    Requires explicit read-only confirmation phrase before any network call.
    Always returns safe_for_mutation=False.
    """
    settings = settings or get_settings()
    started = time.perf_counter()
    checked_at = datetime.now(timezone.utc).isoformat()
    provider_norm = "meta" if provider in {"meta", "facebook"} else (
        "google_ads" if provider in {"google", "google_ads"} else provider
    )

    await write_audit(
        db,
        action="provider.verification_started",
        organization_id=organization_id,
        user_id=actor_user_id,
        resource_type="provider",
        resource_id=provider_norm,
        details={"provider": provider_norm, "trigger": "operator", "phase": "read_only"},
    )
    events.provider_verification(organization_id=organization_id, provider=provider_norm, outcome="started")

    ok_confirm, confirm_reason = require_read_only_confirm(confirm)
    if not ok_confirm:
        report = VerificationReport(
            provider=provider_norm,
            ran=False,
            skipped_reason=confirm_reason,
            status="BLOCKED",
            checked_at=checked_at,
            environment=settings.env,
            authentication={"status": "NOT_CHECKED"},
            authorization={"status": "NOT_CHECKED"},
        )
        await _finish_verification(
            db,
            organization_id=organization_id,
            actor_user_id=actor_user_id,
            report=report,
            client_id=client_id,
            latency_ms=(time.perf_counter() - started) * 1000,
            audit_action="provider.verification_blocked",
        )
        return report

    preflight = await run_provider_preflight(
        db, organization_id=organization_id, provider=provider_norm, client_id=client_id, settings=settings
    )
    await write_audit(
        db,
        action="provider.preflight_completed",
        organization_id=organization_id,
        user_id=actor_user_id,
        resource_type="provider",
        resource_id=provider_norm,
        details={
            "provider": provider_norm,
            "status": preflight.status.value,
            "credentials_configured": preflight.credentials_configured,
            "integration_connected": preflight.integration_connected,
        },
    )

    if preflight.status in {PreflightStatus.not_configured, PreflightStatus.demo}:
        report = VerificationReport(
            provider=provider_norm,
            ran=False,
            skipped_reason="Credentials / connection not configured"
            if preflight.status == PreflightStatus.not_configured
            else "Demo mode — not a live provider verification",
            status="DEMO" if preflight.status == PreflightStatus.demo else "NOT_CONFIGURED",
            checks=[c.as_dict() for c in preflight.checks],
            checked_at=checked_at,
            environment=settings.env,
            authentication={"status": "NOT_CHECKED"},
            authorization={"status": "NOT_CHECKED"},
            safe_for_read=False,
        )
        await _finish_verification(
            db,
            organization_id=organization_id,
            actor_user_id=actor_user_id,
            report=report,
            client_id=client_id,
            latency_ms=(time.perf_counter() - started) * 1000,
            audit_action="provider.verification_failed",
        )
        return report

    if preflight.status == PreflightStatus.partially_configured:
        report = VerificationReport(
            provider=provider_norm,
            ran=False,
            skipped_reason="Partial provider configuration",
            status="PARTIALLY_CONFIGURED",
            checks=[c.as_dict() for c in preflight.checks],
            checked_at=checked_at,
            environment=settings.env,
            authentication={"status": "NOT_CHECKED"},
            authorization={"status": "NOT_CHECKED"},
        )
        await _finish_verification(
            db,
            organization_id=organization_id,
            actor_user_id=actor_user_id,
            report=report,
            client_id=client_id,
            latency_ms=(time.perf_counter() - started) * 1000,
            audit_action="provider.verification_failed",
        )
        return report

    if not preflight.integration_connected:
        report = VerificationReport(
            provider=provider_norm,
            ran=False,
            skipped_reason="Integration not connected (OAuth required)",
            status="NOT_CONNECTED",
            checks=[c.as_dict() for c in preflight.checks],
            checked_at=checked_at,
            environment=settings.env,
            authentication={"status": "NOT_CHECKED"},
            authorization={"status": "NOT_CHECKED"},
        )
        await _finish_verification(
            db,
            organization_id=organization_id,
            actor_user_id=actor_user_id,
            report=report,
            client_id=client_id,
            latency_ms=(time.perf_counter() - started) * 1000,
            audit_action="provider.verification_failed",
        )
        return report

    # Live read-only path
    owns_client = http_client is None
    client = http_client or httpx.AsyncClient(timeout=30.0)
    try:
        if provider_norm == "meta":
            report = await _verify_meta_readonly(
                db,
                organization_id=organization_id,
                client_id=client_id,
                http_client=client,
                preflight=preflight,
                settings=settings,
                checked_at=checked_at,
            )
        else:
            report = await _verify_google_readonly(
                db,
                organization_id=organization_id,
                client_id=client_id,
                http_client=client,
                preflight=preflight,
                settings=settings,
                checked_at=checked_at,
            )
    finally:
        if owns_client and hasattr(client, "aclose"):
            await client.aclose()  # type: ignore[attr-defined]

    report.safe_for_mutation = False
    report.environment = settings.env
    audit = (
        "provider.verification_succeeded"
        if report.status == "VERIFIED"
        else "provider.verification_failed"
    )
    await _finish_verification(
        db,
        organization_id=organization_id,
        actor_user_id=actor_user_id,
        report=report,
        client_id=client_id,
        latency_ms=(time.perf_counter() - started) * 1000,
        audit_action=audit,
    )
    return report


async def _finish_verification(
    db: AsyncSession,
    *,
    organization_id: UUID,
    actor_user_id: UUID | None,
    report: VerificationReport,
    client_id: UUID | None,
    latency_ms: float,
    audit_action: str,
) -> None:
    metrics.increment(
        "provider_verification_total",
        labels={"provider": report.provider, "status": report.status},
    )
    metrics.observe(
        "provider_verification_latency_ms",
        latency_ms,
        labels={"provider": report.provider},
    )
    if report.status == "VERIFIED":
        metrics.increment("provider_verification_success_total", labels={"provider": report.provider})
    elif report.status not in {"BLOCKED"}:
        metrics.increment("provider_verification_failure_total", labels={"provider": report.provider})

    events.provider_verification(
        organization_id=organization_id,
        provider=report.provider,
        outcome=report.status,
    )

    await write_audit(
        db,
        action=audit_action,
        organization_id=organization_id,
        user_id=actor_user_id,
        resource_type="provider",
        resource_id=report.provider,
        details={
            "provider": report.provider,
            "status": report.status,
            "account_id_hash": _hash_id((report.account or {}).get("id")),
            "checks_passed": sum(1 for c in report.checks if c.get("status") == "PASS" or c.get("ok") is True),
            "checks_failed": sum(1 for c in report.checks if c.get("status") == "FAIL" or c.get("ok") is False),
            "safe_for_read": report.safe_for_read,
            "safe_for_mutation": False,
            "error_category": report.error_category,
        },
    )

        # Persist sanitized snapshot on integration.config (no migration).
    row = await get_integration_row(
        db, organization_id=organization_id, provider=report.provider, client_id=client_id
    )
    if (not row) and client_id is not None:
        row = await get_integration_row(
            db, organization_id=organization_id, provider=report.provider, client_id=None
        )
    if row is not None:
        cfg = dict(row.config or {})
        cfg["last_verification"] = sanitize_platform_response(report.as_dict())
        # Persist discovered Meta ad accounts for canary allowlists (no secrets).
        if report.provider == "meta" and report.status == "VERIFIED" and report.canary_resources:
            resources = report.canary_resources or {}
            ad = resources.get("ad_account") or report.account or {}
            camps = resources.get("campaigns") or []
            if ad.get("id"):
                cfg["external_account_id"] = ad.get("id")
                existing = list(cfg.get("ad_accounts") or [])
                ids = {str(a.get("id")) for a in existing if isinstance(a, dict)}
                if str(ad.get("id")) not in ids:
                    existing.insert(
                        0,
                        {
                            "id": ad.get("id"),
                            "name": ad.get("name"),
                            "status": ad.get("status"),
                        },
                    )
                cfg["ad_accounts"] = existing[:50]
            if camps:
                cfg["discovered_campaigns"] = [
                    {"id": c.get("id"), "name": c.get("name"), "status": c.get("status")}
                    for c in camps[:25]
                    if isinstance(c, dict)
                ]
            cfg["discovery_updated_at"] = report.checked_at
        # Persist discovered Google customers/campaigns (M7).
        if report.provider == "google_ads" and report.status == "VERIFIED" and report.canary_resources:
            resources = report.canary_resources or {}
            cust = resources.get("customer") or report.account or {}
            camps = resources.get("campaigns") or []
            if cust.get("id"):
                cid = str(cust.get("id")).replace("-", "")
                cfg["customer_id"] = cid
                cfg["external_account_id"] = cid
                existing = list(cfg.get("customers") or [])
                ids = {str(c.get("id")) for c in existing if isinstance(c, dict)}
                if cid not in ids:
                    existing.insert(0, {"id": cid, "name": cust.get("name")})
                cfg["customers"] = existing[:50]
            if camps:
                cfg["discovered_campaigns"] = [
                    {"id": c.get("id"), "name": c.get("name"), "status": c.get("status")}
                    for c in camps[:25]
                    if isinstance(c, dict)
                ]
            cfg["discovery_updated_at"] = report.checked_at
        row.config = cfg
        await db.flush()


async def _verify_meta_readonly(
    db: AsyncSession,
    *,
    organization_id: UUID,
    client_id: UUID | None,
    http_client: AsyncHttpClient,
    preflight: PreflightResult,
    settings: Settings,
    checked_at: str,
) -> VerificationReport:
    report = VerificationReport(
        provider="meta",
        ran=True,
        status="VERIFICATION_FAILED",
        checked_at=checked_at,
        environment=settings.env,
        checks=[c.as_dict() for c in preflight.checks],
    )
    row = await get_integration_row(db, organization_id=organization_id, provider="meta", client_id=client_id)
    if (not row or not row.secret_ref) and client_id is not None:
        row = await get_integration_row(db, organization_id=organization_id, provider="meta", client_id=None)
    tokens = load_tokens(row) if row else None
    access = (tokens or {}).get("access_token")
    if row and access:
        try:
            from app.integrations.meta_oauth import ensure_meta_access_token

            access = await ensure_meta_access_token(
                db, row, organization_id=organization_id, client_id=client_id
            )
        except Exception:
            pass
    if not access:
        report.authentication = {"status": "AUTHENTICATION_FAILED"}
        report.error_category = VerificationErrorCategory.authentication.value
        report.steps.append(
            VerificationStepResult("authentication", False, "No access_token in encrypted store", category="AUTHENTICATION")
        )
        report.checks.extend([s.as_dict() for s in report.steps])
        return report

    # /me
    try:
        me_resp = await http_client.get(
            f"{META_GRAPH_VERSIONED}/me",
            params={"access_token": access, "fields": "id,name"},
        )
    except httpx.TimeoutException:
        report.error_category = VerificationErrorCategory.timeout.value
        report.steps.append(VerificationStepResult("authentication", False, "Meta /me timed out", category="TIMEOUT"))
        report.authentication = {"status": "PROVIDER_UNAVAILABLE"}
        report.checks.extend([s.as_dict() for s in report.steps])
        return report
    except httpx.HTTPError as exc:
        report.error_category = VerificationErrorCategory.network.value
        report.steps.append(
            VerificationStepResult("authentication", False, _safe_error_detail(exc), category="NETWORK")
        )
        report.authentication = {"status": "PROVIDER_UNAVAILABLE"}
        report.checks.extend([s.as_dict() for s in report.steps])
        return report

    if me_resp.status_code >= 400:
        cat = _classify_http_error(me_resp.status_code, me_resp.text)
        report.error_category = cat.value
        report.authentication = {"status": "AUTHENTICATION_FAILED" if cat == VerificationErrorCategory.authentication else "AUTHORIZATION_FAILED"}
        report.steps.append(
            VerificationStepResult(
                "authentication",
                False,
                f"Meta /me HTTP {me_resp.status_code}",
                category=cat.value,
            )
        )
        report.checks.extend([s.as_dict() for s in report.steps])
        return report

    me = me_resp.json() if hasattr(me_resp, "json") else {}
    report.steps.append(
        VerificationStepResult(
            "authentication",
            True,
            "Meta token accepted by /me",
            observed={"user_id_hash": _hash_id(me.get("id"))},
        )
    )
    report.authentication = {"status": "VERIFIED"}

    # Ad accounts (read-only)
    try:
        accts_resp = await http_client.get(
            f"{META_GRAPH_VERSIONED}/me/adaccounts",
            params={
                "access_token": access,
                "fields": "id,name,account_id,account_status,currency,timezone_name",
            },
        )
    except httpx.TimeoutException:
        report.error_category = VerificationErrorCategory.timeout.value
        report.steps.append(VerificationStepResult("account_identity", False, "adaccounts timed out", category="TIMEOUT"))
        report.checks.extend([s.as_dict() for s in report.steps])
        return report
    except httpx.HTTPError as exc:
        report.error_category = VerificationErrorCategory.network.value
        report.steps.append(
            VerificationStepResult("account_identity", False, _safe_error_detail(exc), category="NETWORK")
        )
        report.checks.extend([s.as_dict() for s in report.steps])
        return report

    if accts_resp.status_code >= 400:
        cat = _classify_http_error(accts_resp.status_code, accts_resp.text)
        report.error_category = cat.value
        report.authorization = {"status": "AUTHORIZATION_FAILED"}
        report.steps.append(
            VerificationStepResult(
                "account_identity",
                False,
                f"Meta adaccounts HTTP {accts_resp.status_code}",
                category=cat.value,
            )
        )
        report.checks.extend([s.as_dict() for s in report.steps])
        return report

    accounts = (accts_resp.json() if hasattr(accts_resp, "json") else {}).get("data") or []
    if not accounts:
        report.error_category = VerificationErrorCategory.account_not_found.value
        report.steps.append(
            VerificationStepResult("account_identity", False, "No accessible Meta ad accounts", category="ACCOUNT_NOT_FOUND")
        )
        report.authorization = {"status": "AUTHORIZATION_FAILED"}
        report.checks.extend([s.as_dict() for s in report.steps])
        return report

    cfg = (row.config or {}) if row else {}
    configured_ext = cfg.get("external_account_id")
    meta_user_id = cfg.get("meta_user_id")
    known_ids = {
        str(a.get("id"))
        for a in (cfg.get("ad_accounts") or [])
        if isinstance(a, dict) and a.get("id")
    }
    accessible_ids: set[str] = set()
    for a in accounts:
        if not isinstance(a, dict):
            continue
        accessible_ids.add(str(a.get("id") or ""))
        if a.get("account_id") is not None:
            accessible_ids.add(str(a.get("account_id")))
            accessible_ids.add(f"act_{a.get('account_id')}")
    accessible_ids.discard("")

    # Prefer the configured ad account when it is still accessible; else first accessible.
    # Legacy OAuth stored Graph user id as external_account_id — do not fail hard on that.
    account = accounts[0]
    if configured_ext and str(configured_ext) in accessible_ids:
        for a in accounts:
            if not isinstance(a, dict):
                continue
            ids = {str(a.get("id") or ""), str(a.get("account_id") or ""), f"act_{a.get('account_id')}"}
            if str(configured_ext) in ids:
                account = a
                break
    elif configured_ext and str(configured_ext) == str(meta_user_id or ""):
        # Legacy user-id identity — ad accounts are still readable; proceed with first.
        pass
    elif configured_ext and known_ids and str(configured_ext) not in accessible_ids and str(configured_ext) not in known_ids:
        report.steps.append(
            VerificationStepResult(
                "account_identity",
                False,
                "Configured external_account_id does not match accessible accounts",
                category="ACCOUNT_ACCESS",
            )
        )
        report.error_category = VerificationErrorCategory.account_access.value
        report.checks.extend([s.as_dict() for s in report.steps])
        return report
    elif configured_ext and str(configured_ext) not in accessible_ids and not meta_user_id and not known_ids:
        # Strict mismatch when we have no legacy/user context
        if not str(configured_ext).startswith("act_") and configured_ext not in accessible_ids:
            # Likely legacy user id without meta_user_id field — allow discovery
            pass
        elif str(configured_ext) not in accessible_ids:
            report.steps.append(
                VerificationStepResult(
                    "account_identity",
                    False,
                    "Configured external_account_id does not match accessible accounts",
                    category="ACCOUNT_ACCESS",
                )
            )
            report.error_category = VerificationErrorCategory.account_access.value
            report.checks.extend([s.as_dict() for s in report.steps])
            return report

    report.account = {
        "id": account.get("id") or account.get("account_id"),
        "name": account.get("name"),
        "currency": account.get("currency"),
        "timezone": account.get("timezone_name"),
        "status": account.get("account_status"),
    }
    report.steps.append(
        VerificationStepResult(
            "account_identity",
            True,
            "Ad account accessible",
            observed={"account_id_hash": _hash_id(str(report.account.get("id")))},
        )
    )

    # Campaign discovery for canary allowlist configuration (read-only)
    account_id = account.get("id")
    caps: list[dict[str, Any]] = [
        {"name": "READ_ACCOUNT", "status": "VERIFIED"},
        {"name": "PAUSE_CAMPAIGN", "status": "SUPPORTED"},
        {"name": "RESUME_CAMPAIGN", "status": "SUPPORTED"},
    ]
    campaigns: list[dict[str, Any]] = []
    if account_id:
        try:
            camp_resp = await http_client.get(
                f"{META_GRAPH_VERSIONED}/{account_id}/campaigns",
                params={"access_token": access, "fields": "id,name,status,effective_status", "limit": 25},
            )
            if camp_resp.status_code < 400:
                caps.append({"name": "READ_CAMPAIGNS", "status": "VERIFIED"})
                report.steps.append(VerificationStepResult("read_campaigns", True, "Campaigns read OK"))
                raw = (camp_resp.json() if hasattr(camp_resp, "json") else {}).get("data") or []
                for c in raw[:25]:
                    if not isinstance(c, dict):
                        continue
                    campaigns.append(
                        {
                            "id": c.get("id"),
                            "name": c.get("name"),
                            "status": c.get("effective_status") or c.get("status"),
                        }
                    )
            else:
                caps.append({"name": "READ_CAMPAIGNS", "status": "FAILED"})
                report.steps.append(
                    VerificationStepResult(
                        "read_campaigns",
                        False,
                        f"campaigns HTTP {camp_resp.status_code}",
                        category=_classify_http_error(camp_resp.status_code, camp_resp.text).value,
                    )
                )
        except Exception as exc:
            caps.append({"name": "READ_CAMPAIGNS", "status": "FAILED"})
            report.steps.append(
                VerificationStepResult("read_campaigns", False, _safe_error_detail(exc), category="API_ERROR")
            )

    report.canary_resources = {
        "ad_account": {
            "id": report.account.get("id"),
            "name": report.account.get("name"),
            "status": report.account.get("status"),
        },
        "campaigns": campaigns,
        "supported_capabilities": ["pause_campaign", "resume_campaign", "get_status"],
    }
    report.capabilities = caps
    report.authorization = {"status": "VERIFIED", "capabilities": caps}
    report.safe_for_read = True
    report.safe_for_mutation = False
    report.status = "VERIFIED"
    report.checks.extend([s.as_dict() for s in report.steps])
    return report


async def _verify_google_readonly(
    db: AsyncSession,
    *,
    organization_id: UUID,
    client_id: UUID | None,
    http_client: AsyncHttpClient,
    preflight: PreflightResult,
    settings: Settings,
    checked_at: str,
) -> VerificationReport:
    report = VerificationReport(
        provider="google_ads",
        ran=True,
        status="VERIFICATION_FAILED",
        checked_at=checked_at,
        environment=settings.env,
        checks=[c.as_dict() for c in preflight.checks],
    )
    row = await get_integration_row(
        db, organization_id=organization_id, provider="google_ads", client_id=client_id
    )
    if (not row or not row.secret_ref) and client_id is not None:
        row = await get_integration_row(
            db, organization_id=organization_id, provider="google_ads", client_id=None
        )
    if row is None:
        report.authentication = {"status": "AUTHENTICATION_FAILED"}
        report.error_category = VerificationErrorCategory.configuration.value
        report.steps.append(VerificationStepResult("authentication", False, "No google_ads integration row"))
        report.checks.extend([s.as_dict() for s in report.steps])
        return report

    try:
        # Always refresh when stale (M7 parity with Meta ensure_meta_access_token).
        access = await ensure_access_token(
            db, row, organization_id=organization_id, provider="google_ads", client_id=client_id
        )
    except Exception as exc:
        tokens = load_tokens(row) or {}
        access = tokens.get("access_token")
        if not access:
            report.authentication = {"status": "AUTHENTICATION_FAILED"}
            report.error_category = VerificationErrorCategory.authentication.value
            report.steps.append(
                VerificationStepResult("authentication", False, _safe_error_detail(exc), category="AUTHENTICATION")
            )
            report.checks.extend([s.as_dict() for s in report.steps])
            return report

    headers = {
        "Authorization": f"Bearer {access}",
        "developer-token": settings.google_ads_developer_token,
    }
    if settings.google_ads_login_customer_id:
        headers["login-customer-id"] = settings.google_ads_login_customer_id.replace("-", "")

    try:
        resp = await http_client.get(f"{GOOGLE_ADS_API}/customers:listAccessibleCustomers", headers=headers)
    except httpx.TimeoutException:
        report.error_category = VerificationErrorCategory.timeout.value
        report.authentication = {"status": "PROVIDER_UNAVAILABLE"}
        report.steps.append(VerificationStepResult("authentication", False, "listAccessibleCustomers timed out", category="TIMEOUT"))
        report.checks.extend([s.as_dict() for s in report.steps])
        return report
    except httpx.HTTPError as exc:
        report.error_category = VerificationErrorCategory.network.value
        report.authentication = {"status": "PROVIDER_UNAVAILABLE"}
        report.steps.append(VerificationStepResult("authentication", False, _safe_error_detail(exc), category="NETWORK"))
        report.checks.extend([s.as_dict() for s in report.steps])
        return report

    if resp.status_code >= 400:
        cat = _classify_http_error(resp.status_code, resp.text)
        report.error_category = cat.value
        report.authentication = {
            "status": "AUTHENTICATION_FAILED"
            if cat == VerificationErrorCategory.authentication
            else "AUTHORIZATION_FAILED"
        }
        report.steps.append(
            VerificationStepResult(
                "authentication",
                False,
                f"listAccessibleCustomers HTTP {resp.status_code}",
                category=cat.value,
            )
        )
        report.checks.extend([s.as_dict() for s in report.steps])
        return report

    names = (resp.json() if hasattr(resp, "json") else {}).get("resourceNames") or []
    report.steps.append(
        VerificationStepResult(
            "authentication",
            True,
            "Google Ads API accepted developer token + OAuth",
            observed={"accessible_customers": len(names)},
        )
    )
    report.authentication = {"status": "VERIFIED"}

    if not names:
        report.error_category = VerificationErrorCategory.account_not_found.value
        report.steps.append(
            VerificationStepResult("account_identity", False, "No accessible Google Ads customers", category="ACCOUNT_NOT_FOUND")
        )
        report.authorization = {"status": "AUTHORIZATION_FAILED"}
        report.checks.extend([s.as_dict() for s in report.steps])
        return report

    customer_id = str(names[0]).split("/")[-1]
    configured = (row.config or {}).get("external_account_id") or (row.config or {}).get("customer_id")
    if configured and str(configured).replace("-", "") not in {customer_id, str(configured)}:
        # Prefer first accessible if config missing; fail only on hard mismatch
        cfg_norm = str(configured).replace("-", "")
        accessible = {str(n).split("/")[-1] for n in names}
        if cfg_norm not in accessible:
            report.error_category = VerificationErrorCategory.account_access.value
            report.steps.append(
                VerificationStepResult(
                    "account_identity",
                    False,
                    "Configured customer ID not in accessible customers",
                    category="ACCOUNT_ACCESS",
                )
            )
            report.checks.extend([s.as_dict() for s in report.steps])
            return report
        customer_id = cfg_norm

    report.account = {"id": customer_id, "name": f"Google Ads / {customer_id}"}
    report.steps.append(
        VerificationStepResult(
            "account_identity",
            True,
            "Customer accessible",
            observed={"account_id_hash": _hash_id(customer_id)},
        )
    )

    caps = [
        {"name": "READ_CUSTOMER", "status": "VERIFIED"},
        {"name": "PAUSE_CAMPAIGN", "status": "SUPPORTED"},
        {"name": "RESUME_CAMPAIGN", "status": "SUPPORTED"},
        {"name": "UPDATE_BUDGET", "status": "UNSUPPORTED"},
    ]
    campaigns: list[dict[str, Any]] = []
    # Read-only GAQL: campaign discovery for canary allowlists
    search_headers = {**headers, "Content-Type": "application/json"}
    query = "SELECT campaign.id, campaign.name, campaign.status FROM campaign LIMIT 25"
    try:
        search = await http_client.post(
            f"{GOOGLE_ADS_API}/customers/{customer_id}/googleAds:search",
            headers=search_headers,
            json={"query": query},
        )
        if search.status_code < 400:
            caps.append({"name": "READ_CAMPAIGNS", "status": "VERIFIED"})
            report.steps.append(VerificationStepResult("read_campaigns", True, "Campaign search OK"))
            results = (search.json() if hasattr(search, "json") else {}).get("results") or []
            for row_data in results[:25]:
                if not isinstance(row_data, dict):
                    continue
                camp = row_data.get("campaign") or {}
                campaigns.append(
                    {
                        "id": str(camp.get("id")) if camp.get("id") is not None else None,
                        "name": camp.get("name"),
                        "status": camp.get("status"),
                    }
                )
        else:
            caps.append({"name": "READ_CAMPAIGNS", "status": "FAILED"})
            report.steps.append(
                VerificationStepResult(
                    "read_campaigns",
                    False,
                    f"search HTTP {search.status_code}",
                    category=_classify_http_error(search.status_code, search.text).value,
                )
            )
    except Exception as exc:
        caps.append({"name": "READ_CAMPAIGNS", "status": "FAILED"})
        report.steps.append(VerificationStepResult("read_campaigns", False, _safe_error_detail(exc), category="API_ERROR"))

    report.canary_resources = {
        "customer": {"id": customer_id, "name": report.account.get("name")},
        "campaigns": campaigns,
        "supported_capabilities": ["pause_campaign", "resume_campaign", "get_metrics"],
    }
    report.capabilities = caps
    report.authorization = {"status": "VERIFIED", "capabilities": caps}
    report.safe_for_read = True
    report.safe_for_mutation = False
    report.status = "VERIFIED"
    report.checks.extend([s.as_dict() for s in report.steps])
    return report


# ---- M4 CLI stubs (mutation path still not executed) ----------------------


async def verify_meta_campaign_ops(*, dry_run: bool = True) -> VerificationReport:
    settings = get_settings()
    ok, reason = verification_preflight(settings)
    if not ok:
        return VerificationReport(provider="meta", ran=False, skipped_reason=reason, status="BLOCKED")

    campaign_id = (settings.provider_verification_meta_campaign_id or "").strip()
    if not campaign_id:
        return VerificationReport(
            provider="meta",
            ran=False,
            skipped_reason="PROVIDER_VERIFICATION_META_CAMPAIGN_ID required",
            status="BLOCKED",
        )
    if not (settings.meta_app_id and settings.meta_app_secret):
        return VerificationReport(
            provider="meta",
            ran=False,
            skipped_reason="LIVE PROVIDER VERIFICATION NOT RUN — CREDENTIALS REQUIRED",
            status="NOT_CONFIGURED",
        )

    report = VerificationReport(provider="meta", ran=True, status="BLOCKED")
    report.steps.append(
        VerificationStepResult(
            "preflight",
            True,
            "confirmation accepted; Phase 1 is READ-ONLY (mutations not run); dry_run=%s" % dry_run,
            observed={"campaign_id": campaign_id},
        )
    )
    report.steps.append(
        VerificationStepResult(
            "live_mutation",
            False,
            "Phase 1 refuses mutations — use operator read-only verify instead",
            observed={"dry_run": dry_run},
        )
    )
    report.safe_for_mutation = False
    return report


async def verify_google_campaign_ops(*, dry_run: bool = True) -> VerificationReport:
    settings = get_settings()
    ok, reason = verification_preflight(settings)
    if not ok:
        return VerificationReport(provider="google", ran=False, skipped_reason=reason, status="BLOCKED")

    campaign_id = (settings.provider_verification_google_campaign_id or "").strip()
    if not campaign_id:
        return VerificationReport(
            provider="google",
            ran=False,
            skipped_reason="PROVIDER_VERIFICATION_GOOGLE_CAMPAIGN_ID required",
            status="BLOCKED",
        )
    if not (
        settings.google_client_id
        and settings.google_client_secret
        and settings.google_ads_developer_token
    ):
        return VerificationReport(
            provider="google",
            ran=False,
            skipped_reason="LIVE PROVIDER VERIFICATION NOT RUN — CREDENTIALS REQUIRED",
            status="NOT_CONFIGURED",
        )

    report = VerificationReport(provider="google", ran=True, status="BLOCKED")
    report.steps.append(
        VerificationStepResult(
            "preflight",
            True,
            "confirmation accepted; Phase 1 is READ-ONLY; dry_run=%s" % dry_run,
            observed={"campaign_id": campaign_id},
        )
    )
    report.steps.append(
        VerificationStepResult(
            "live_mutation",
            False,
            "Phase 1 refuses mutations; Google budget update remains unsupported",
            observed={"dry_run": dry_run, "budget_update": "UNSUPPORTED"},
        )
    )
    report.safe_for_mutation = False
    return report
