"""Shared types for SEO approval/actions (M9.13)."""

from __future__ import annotations

import enum

SEO_ACTION_VERSION = "seo_action_v1"


class SeoActionSourceType(str, enum.Enum):
    internal_link_opportunity = "internal_link_opportunity"
    onpage_finding = "onpage_finding"
    schema_artifact = "schema_artifact"
    generated_content = "generated_content"


class SeoExecutionCapability(str, enum.Enum):
    review_only = "review_only"
    executable = "executable"
    unsupported = "unsupported"
    blocked = "blocked"
