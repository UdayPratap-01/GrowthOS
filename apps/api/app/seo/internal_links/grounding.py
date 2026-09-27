"""AI output grounding for internal-link suggestions (M9.12)."""

from __future__ import annotations

from app.schemas.seo_internal_link import SeoInternalLinkAiOutput
from app.seo.internal_links.types import InternalLinkOpportunityDraft
from app.seo.internal_links.url_validation import is_internal_url


class InternalLinkGroundingError(Exception):
    pass


def apply_ai_enrichment(
    drafts: list[InternalLinkOpportunityDraft],
    output: SeoInternalLinkAiOutput,
    *,
    site_root: str,
) -> None:
    by_key = {(d.source_url, d.target_url): d for d in drafts}
    for suggestion in output.suggestions:
        key = (suggestion.source_url, suggestion.target_url)
        if key not in by_key:
            raise InternalLinkGroundingError(f"AI suggested unknown pair: {key}")
        if not is_internal_url(suggestion.source_url, site_root=site_root):
            raise InternalLinkGroundingError("AI suggested external/invalid source URL")
        if not is_internal_url(suggestion.target_url, site_root=site_root):
            raise InternalLinkGroundingError("AI suggested external/invalid target URL")
        draft = by_key[key]
        if suggestion.rationale:
            draft.relationship_reason = suggestion.rationale[:2000]
        if suggestion.anchor_alternatives:
            merged = list(dict.fromkeys([draft.anchor_text] + suggestion.anchor_alternatives))
            draft.anchor_alternatives = merged[1:5]
