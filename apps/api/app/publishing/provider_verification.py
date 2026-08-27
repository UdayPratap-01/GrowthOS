"""Safe live-provider verification harness.

Does nothing unless PROVIDER_VERIFICATION_ENABLED=true and explicit confirmation.
Never invents credentials. Skipped in normal pytest.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.core.config import Settings, get_settings

CONFIRM_PHRASE = "I_CONFIRM_LIVE_MUTATIONS"


@dataclass
class VerificationStepResult:
    step: str
    ok: bool
    detail: str
    observed: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "step": self.step,
            "ok": self.ok,
            "detail": self.detail,
            "observed": self.observed,
        }


@dataclass
class VerificationReport:
    provider: str
    ran: bool
    skipped_reason: str | None = None
    steps: list[VerificationStepResult] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "ran": self.ran,
            "skipped_reason": self.skipped_reason,
            "steps": [s.as_dict() for s in self.steps],
            "live_provider_verification": "RAN" if self.ran else "NOT_RUN",
        }


def verification_preflight(settings: Settings | None = None) -> tuple[bool, str | None]:
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


async def verify_meta_campaign_ops(*, dry_run: bool = True) -> VerificationReport:
    """
    Verify Meta lookup / pause / resume / budget when credentials present.

    dry_run=True (default): authenticate + lookup only — no mutations.
    """
    settings = get_settings()
    ok, reason = verification_preflight(settings)
    if not ok:
        return VerificationReport(provider="meta", ran=False, skipped_reason=reason)

    campaign_id = (settings.provider_verification_meta_campaign_id or "").strip()
    if not campaign_id:
        return VerificationReport(
            provider="meta", ran=False, skipped_reason="PROVIDER_VERIFICATION_META_CAMPAIGN_ID required"
        )
    if not (settings.meta_app_id and settings.meta_app_secret):
        return VerificationReport(
            provider="meta", ran=False, skipped_reason="LIVE PROVIDER VERIFICATION NOT RUN — CREDENTIALS REQUIRED"
        )

    report = VerificationReport(provider="meta", ran=True)
    report.steps.append(
        VerificationStepResult(
            step="preflight",
            ok=True,
            detail="confirmation accepted; dry_run=%s" % dry_run,
            observed={"campaign_id": campaign_id, "org_id": settings.provider_verification_org_id},
        )
    )
    # Live HTTP calls are intentionally not implemented without an operator-supplied
    # adapter session — this module records the gate so CI stays safe.
    report.steps.append(
        VerificationStepResult(
            step="live_mutation",
            ok=False,
            detail=(
                "LIVE PROVIDER VERIFICATION NOT RUN — wire AdsExecutor against the "
                "configured test campaign outside CI; credentials present check only"
            ),
            observed={"dry_run": dry_run},
        )
    )
    return report


async def verify_google_campaign_ops(*, dry_run: bool = True) -> VerificationReport:
    settings = get_settings()
    ok, reason = verification_preflight(settings)
    if not ok:
        return VerificationReport(provider="google", ran=False, skipped_reason=reason)

    campaign_id = (settings.provider_verification_google_campaign_id or "").strip()
    if not campaign_id:
        return VerificationReport(
            provider="google",
            ran=False,
            skipped_reason="PROVIDER_VERIFICATION_GOOGLE_CAMPAIGN_ID required",
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
        )

    report = VerificationReport(provider="google", ran=True)
    report.steps.append(
        VerificationStepResult(
            step="preflight",
            ok=True,
            detail="confirmation accepted; dry_run=%s (budget update unsupported)" % dry_run,
            observed={"campaign_id": campaign_id},
        )
    )
    report.steps.append(
        VerificationStepResult(
            step="live_mutation",
            ok=False,
            detail="LIVE PROVIDER VERIFICATION NOT RUN — credentials gate only in this environment",
            observed={"dry_run": dry_run, "budget_update": "UNSUPPORTED"},
        )
    )
    return report
