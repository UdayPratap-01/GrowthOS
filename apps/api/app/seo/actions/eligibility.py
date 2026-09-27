"""Deterministic eligibility checks for SEO actions (M9.13)."""

from __future__ import annotations

from dataclasses import dataclass, field
from uuid import UUID

from app.models.enums import AIActionType
from app.seo.actions.capabilities import resolve_execution_capability
from app.seo.actions.types import SeoActionSourceType, SeoExecutionCapability
from app.seo.internal_links.url_validation import is_internal_url, normalize_internal_url


@dataclass
class EligibilityResult:
    eligible: bool
    capability: SeoExecutionCapability
    reasons: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def check_url_eligibility(url: str | None, *, site_root: str | None, label: str) -> list[str]:
    if not url:
        return [f"{label}_MISSING"]
    if not site_root:
        return [f"{label}_SITE_ROOT_UNKNOWN"]
    normalized = normalize_internal_url(url, site_root=site_root)
    if normalized is None or not is_internal_url(normalized, site_root=site_root):
        return [f"{label}_NOT_INTERNAL"]
    return []


def evaluate_eligibility(
    *,
    action_type: AIActionType,
    source_type: SeoActionSourceType,
    source_status: str,
    site_root: str | None,
    source_url: str | None = None,
    target_url: str | None = None,
    has_evidence: bool,
    archived: bool = False,
) -> EligibilityResult:
    capability = resolve_execution_capability(action_type=action_type)
    reasons: list[str] = []

    if archived:
        reasons.append("ARTIFACT_ARCHIVED")
    if capability == SeoExecutionCapability.unsupported:
        reasons.append("UNSUPPORTED_ACTION_TYPE")
    if not has_evidence:
        reasons.append("EVIDENCE_REQUIRED")

    allowed_statuses = {
        SeoActionSourceType.internal_link_opportunity: {"suggested"},
        SeoActionSourceType.onpage_finding: {"open"},
        SeoActionSourceType.schema_artifact: {"validated"},
        SeoActionSourceType.generated_content: {"draft"},
    }
    if source_status not in allowed_statuses.get(source_type, set()):
        reasons.append(f"INVALID_SOURCE_STATUS:{source_status}")

    if source_type == SeoActionSourceType.internal_link_opportunity:
        reasons.extend(check_url_eligibility(source_url, site_root=site_root, label="SOURCE_URL"))
        reasons.extend(check_url_eligibility(target_url, site_root=site_root, label="TARGET_URL"))
        if source_url and target_url and source_url == target_url:
            reasons.append("SELF_LINK")

    if source_type == SeoActionSourceType.schema_artifact and source_status == "draft":
        reasons.append("SCHEMA_NOT_VALIDATED")

    eligible = not reasons and capability != SeoExecutionCapability.blocked
    return EligibilityResult(eligible=eligible, capability=capability, reasons=reasons)
