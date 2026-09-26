"""Deterministic Search Console opportunity detection."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.seo.search_console.comparison import pct_change, position_change
from app.seo.search_console.thresholds import DEFAULT_GSC_THRESHOLDS, GscIntelligenceThresholds


@dataclass
class OpportunityDraft:
    rule_id: str
    opportunity_type: str
    priority: str
    query: str | None = None
    page_url: str | None = None
    clicks: float = 0
    impressions: float = 0
    ctr: float = 0
    average_position: float = 0
    comparison: dict[str, Any] = field(default_factory=dict)
    evidence: dict[str, Any] = field(default_factory=dict)
    explanation: str = ""
    dedupe_key: str = ""

    def fingerprint(self) -> str:
        return self.dedupe_key or f"{self.rule_id}|{self.query or ''}|{self.page_url or ''}"


def detect_opportunities(
    rows: list[dict[str, Any]],
    *,
    thresholds: GscIntelligenceThresholds = DEFAULT_GSC_THRESHOLDS,
) -> list[OpportunityDraft]:
    drafts: list[OpportunityDraft] = []
    for row in rows:
        drafts.extend(_row_opportunities(row, thresholds))
    return _dedupe(drafts)


def _dedupe(drafts: list[OpportunityDraft]) -> list[OpportunityDraft]:
    seen: set[str] = set()
    out: list[OpportunityDraft] = []
    for draft in drafts:
        key = draft.fingerprint()
        if key in seen:
            continue
        seen.add(key)
        out.append(draft)
    return out


def _row_opportunities(row: dict[str, Any], t: GscIntelligenceThresholds) -> list[OpportunityDraft]:
    impressions = float(row.get("impressions") or 0)
    clicks = float(row.get("clicks") or 0)
    ctr = float(row.get("ctr") or 0)
    position = float(row.get("position") or 0)
    query = row.get("query")
    page = row.get("page")
    if impressions < t.min_impressions:
        return []

    cmp_clicks = row.get("compare_clicks")
    cmp_impressions = row.get("compare_impressions")
    cmp_ctr = row.get("compare_ctr")
    cmp_position = row.get("compare_position")
    click_cmp = pct_change(clicks, cmp_clicks if cmp_clicks is not None else None)
    imp_cmp = pct_change(impressions, cmp_impressions if cmp_impressions is not None else None)
    ctr_cmp = pct_change(ctr, cmp_ctr if cmp_ctr is not None else None)
    pos_cmp = position_change(position, cmp_position if cmp_position is not None else None)

    comparison = {
        "clicks": click_cmp,
        "impressions": imp_cmp,
        "ctr": ctr_cmp,
        "position": pos_cmp,
    }
    evidence_base = {
        "clicks": clicks,
        "impressions": impressions,
        "ctr": ctr,
        "position": position,
        "thresholds": {
            "min_impressions": t.min_impressions,
            "low_ctr": t.low_ctr,
            "declining_pct": t.declining_pct,
        },
        "data_source": "search_console_api",
    }
    out: list[OpportunityDraft] = []

    if query and ctr < t.low_ctr and impressions >= t.min_impressions:
        out.append(
            OpportunityDraft(
                rule_id="GSC_OPP_LOW_CTR_QUERY",
                opportunity_type="low_ctr_query",
                priority="medium" if impressions >= t.strong_impressions else "low",
                query=str(query),
                clicks=clicks,
                impressions=impressions,
                ctr=ctr,
                average_position=position,
                comparison=comparison,
                evidence={**evidence_base, "dimension": "query"},
                explanation=(
                    f'Query "{query}" has {int(impressions)} impressions but CTR {ctr:.2%} '
                    f"(below heuristic threshold {t.low_ctr:.2%})."
                ),
                dedupe_key=f"GSC_OPP_LOW_CTR_QUERY|{query}",
            )
        )

    if page and ctr < t.low_ctr and impressions >= t.min_impressions:
        out.append(
            OpportunityDraft(
                rule_id="GSC_OPP_LOW_CTR_PAGE",
                opportunity_type="low_ctr_page",
                priority="medium" if impressions >= t.strong_impressions else "low",
                page_url=str(page),
                clicks=clicks,
                impressions=impressions,
                ctr=ctr,
                average_position=position,
                comparison=comparison,
                evidence={**evidence_base, "dimension": "page"},
                explanation=(
                    f"Page {page} has {int(impressions)} impressions but CTR {ctr:.2%} "
                    f"(below heuristic threshold {t.low_ctr:.2%})."
                ),
                dedupe_key=f"GSC_OPP_LOW_CTR_PAGE|{page}",
            )
        )

    if query and t.page_boundary_min <= position <= t.page_boundary_max and impressions >= t.min_impressions:
        out.append(
            OpportunityDraft(
                rule_id="GSC_OPP_PAGE_BOUNDARY",
                opportunity_type="ranking_boundary",
                priority="medium",
                query=str(query),
                clicks=clicks,
                impressions=impressions,
                ctr=ctr,
                average_position=position,
                comparison=comparison,
                evidence={
                    **evidence_base,
                    "position_range": [t.page_boundary_min, t.page_boundary_max],
                },
                explanation=(
                    f'Query "{query}" averages position {position:.1f} (page 1/2 boundary heuristic '
                    f"{t.page_boundary_min}-{t.page_boundary_max}) with {int(impressions)} impressions."
                ),
                dedupe_key=f"GSC_OPP_PAGE_BOUNDARY|{query}",
            )
        )

    if impressions >= t.strong_impressions and clicks < impressions * t.low_ctr:
        out.append(
            OpportunityDraft(
                rule_id="GSC_OPP_STRONG_IMPRESSIONS_WEAK_CLICKS",
                opportunity_type="impressions_clicks_gap",
                priority="medium",
                query=str(query) if query else None,
                page_url=str(page) if page else None,
                clicks=clicks,
                impressions=impressions,
                ctr=ctr,
                average_position=position,
                comparison=comparison,
                evidence=evidence_base,
                explanation=(
                    f"Strong impressions ({int(impressions)}) with relatively weak clicks ({int(clicks)})."
                ),
                dedupe_key=f"GSC_OPP_STRONG_IMPRESSIONS_WEAK_CLICKS|{query or ''}|{page or ''}",
            )
        )

    if position <= t.strong_position_max and ctr < t.low_ctr and impressions >= t.min_impressions:
        out.append(
            OpportunityDraft(
                rule_id="GSC_OPP_STRONG_POSITION_WEAK_CTR",
                opportunity_type="position_ctr_gap",
                priority="low",
                query=str(query) if query else None,
                page_url=str(page) if page else None,
                clicks=clicks,
                impressions=impressions,
                ctr=ctr,
                average_position=position,
                comparison=comparison,
                evidence={**evidence_base, "strong_position_max": t.strong_position_max},
                explanation=(
                    f"Average position {position:.1f} with CTR {ctr:.2%} below heuristic {t.low_ctr:.2%}."
                ),
                dedupe_key=f"GSC_OPP_STRONG_POSITION_WEAK_CTR|{query or ''}|{page or ''}",
            )
        )

    if page and cmp_clicks is not None and clicks >= t.min_clicks_for_decline:
        change = click_cmp.get("change_pct")
        if change is not None and change <= t.declining_pct:
            out.append(
                OpportunityDraft(
                    rule_id="GSC_OPP_DECLINING_CLICKS_PAGE",
                    opportunity_type="declining_clicks",
                    priority="medium",
                    page_url=str(page),
                    clicks=clicks,
                    impressions=impressions,
                    ctr=ctr,
                    average_position=position,
                    comparison=comparison,
                    evidence={**evidence_base, "clicks_change_pct": change},
                    explanation=f"Page clicks declined {change:.1f}% vs previous equivalent period.",
                    dedupe_key=f"GSC_OPP_DECLINING_CLICKS_PAGE|{page}",
                )
            )

    if page and cmp_impressions is not None:
        change = imp_cmp.get("change_pct")
        if change is not None and change <= t.declining_pct and impressions >= t.min_impressions:
            out.append(
                OpportunityDraft(
                    rule_id="GSC_OPP_DECLINING_IMPRESSIONS_PAGE",
                    opportunity_type="declining_impressions",
                    priority="medium",
                    page_url=str(page),
                    clicks=clicks,
                    impressions=impressions,
                    ctr=ctr,
                    average_position=position,
                    comparison=comparison,
                    evidence={**evidence_base, "impressions_change_pct": change},
                    explanation=f"Page impressions declined {change:.1f}% vs previous equivalent period.",
                    dedupe_key=f"GSC_OPP_DECLINING_IMPRESSIONS_PAGE|{page}",
                )
            )

    if (
        query
        and page
        and impressions >= t.strong_impressions
        and (ctr < t.low_ctr or t.page_boundary_min <= position <= t.page_boundary_max)
    ):
        out.append(
            OpportunityDraft(
                rule_id="GSC_OPP_QUERY_PAGE_COMBO",
                opportunity_type="query_page_combo",
                priority="low",
                query=str(query),
                page_url=str(page),
                clicks=clicks,
                impressions=impressions,
                ctr=ctr,
                average_position=position,
                comparison=comparison,
                evidence=evidence_base,
                explanation="Query/page combination with meaningful impressions and heuristic opportunity signals.",
                dedupe_key=f"GSC_OPP_QUERY_PAGE_COMBO|{query}|{page}",
            )
        )

    return out
