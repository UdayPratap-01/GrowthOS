"""Execution capability detection for SEO actions (M9.13)."""

from __future__ import annotations

from app.models.enums import AIActionType
from app.seo.actions.types import SeoExecutionCapability

# No CMS/site writer integration exists today — all SEO mutations are review-only.
_SUPPORTED_WRITERS: set[str] = set()


def resolve_execution_capability(*, action_type: AIActionType) -> SeoExecutionCapability:
    if action_type not in {
        AIActionType.seo_apply_metadata,
        AIActionType.seo_apply_internal_link,
        AIActionType.seo_apply_schema,
        AIActionType.seo_apply_content,
    }:
        return SeoExecutionCapability.unsupported
    if not _SUPPORTED_WRITERS:
        return SeoExecutionCapability.review_only
    return SeoExecutionCapability.executable
