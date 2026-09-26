"""Deterministic keyword opportunity rules."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.seo.keywords.aggregate import AggregatedQuery, QueryPageRow
from app.seo.keywords.normalize import is_long_tail
from app.seo.keywords.priority import compute_priority_score
from app.seo.keywords.thresholds import DEFAULT_KEYWORD_THRESHOLDS, KeywordOpportunityThresholds
from app.seo.search_console.comparison import pct_change


@dataclass
class KeywordOpportunityDraft:
    rule_id: str
    opportunity_type: str
    query: str
    normalized_query: str
    page_url: str | None = None
    priority: str = "info"
    priority_score: float = 0.0
    clicks: float = 0
    impressions: float = 0
    ctr: float = 0
    average_position: float = 0
    previous_clicks: float | None = None
    previous_impressions: float | None = None
    previous_ctr: float | None = None
    previous_average_position: float | None = None
    change_metrics: dict[str, Any] = field(default_factory=dict)
    query_classification: dict[str, Any] = field(default_factory=dict)
    page_associations: list[str] = field(default_factory=list)
    evidence: dict[str, Any] = field(default_factory=dict)
    explanation: str = ""
    dedupe_key: str = ""


def classify_branded(query: str, brand_terms: list[str] | None) -> str:
    if not brand_terms:
        return "unknown"
    q = query.casefold()
    for term in brand_terms:
        if term and term.casefold() in q:
            return "branded"
    return "non_branded"


def detect_keyword_opportunities(
    queries: list[AggregatedQuery],
    query_pages: list[QueryPageRow],
    *,
    brand_terms: list[str] | None = None,
    thresholds: KeywordOpportunityThresholds = DEFAULT_KEYWORD_THRESHOLDS,
) -> list[KeywordOpportunityDraft]:
    drafts: list[KeywordOpportunityDraft] = []
    for agg in queries:
        drafts.extend(_query_opportunities(agg, brand_terms=brand_terms, thresholds=thresholds))
    for qp in query_pages:
        drafts.extend(_page_query_opportunities(qp, brand_terms=brand_terms, thresholds=thresholds))
    return _dedupe(drafts)


def _dedupe(drafts: list[KeywordOpportunityDraft]) -> list[KeywordOpportunityDraft]:
    seen: set[str] = set()
    out: list[KeywordOpportunityDraft] = []
    for d in drafts:
        key = d.dedupe_key or f"{d.rule_id}|{d.normalized_query}|{d.page_url or ''}"
        if key in seen:
            continue
        seen.add(key)
        out.append(d)
    return out


def _base_classification(query: str, brand_terms: list[str] | None, t: KeywordOpportunityThresholds) -> dict[str, Any]:
    return {
        "branded_status": classify_branded(query, brand_terms),
        "long_tail": is_long_tail(query, min_tokens=t.long_tail_min_tokens),
        "long_tail_note": "Token count is a structural characteristic, not search volume or intent.",
    }


def _query_opportunities(
    agg: AggregatedQuery,
    *,
    brand_terms: list[str] | None,
    thresholds: KeywordOpportunityThresholds,
) -> list[KeywordOpportunityDraft]:
    t = thresholds
    if agg.impressions < t.min_impressions:
        return []

    priority, score = compute_priority_score(
        impressions=agg.impressions, ctr=agg.ctr, average_position=agg.average_position, low_ctr=t.low_ctr
    )
    classification = _base_classification(agg.query, brand_terms, t)
    change = {
        "clicks": pct_change(agg.clicks, agg.compare_clicks),
        "impressions": pct_change(agg.impressions, agg.compare_impressions),
        "ctr": pct_change(agg.ctr, agg.compare_ctr),
    }
    pages = [p.page_url for p in agg.pages]
    evidence = {
        "data_source": "search_console_performance",
        "impressions_note": "Impressions are Search Console performance, not keyword search volume.",
        "thresholds": {
            "min_impressions": t.min_impressions,
            "low_ctr": t.low_ctr,
        },
    }
    out: list[KeywordOpportunityDraft] = []

    if agg.ctr < t.low_ctr:
        out.append(
            KeywordOpportunityDraft(
                rule_id="KW_OPP_HIGH_IMPRESSION_LOW_CTR",
                opportunity_type="high_impression_low_ctr",
                query=agg.query,
                normalized_query=agg.normalized_query,
                priority=priority,
                priority_score=score,
                clicks=agg.clicks,
                impressions=agg.impressions,
                ctr=agg.ctr,
                average_position=agg.average_position,
                previous_clicks=agg.compare_clicks,
                previous_impressions=agg.compare_impressions,
                previous_ctr=agg.compare_ctr,
                previous_average_position=agg.compare_position,
                change_metrics=change,
                query_classification=classification,
                page_associations=pages,
                evidence=evidence,
                explanation=(
                    f'Query "{agg.query}" has {int(agg.impressions)} Search Console impressions '
                    f"with CTR {agg.ctr:.2%} (below heuristic {t.low_ctr:.2%})."
                ),
                dedupe_key=f"KW_OPP_HIGH_IMPRESSION_LOW_CTR|{agg.normalized_query}",
            )
        )

    if t.near_page_one_min <= agg.average_position <= t.near_page_one_max:
        out.append(
            KeywordOpportunityDraft(
                rule_id="KW_OPP_NEAR_PAGE_ONE",
                opportunity_type="near_page_one",
                query=agg.query,
                normalized_query=agg.normalized_query,
                priority="medium",
                priority_score=score,
                clicks=agg.clicks,
                impressions=agg.impressions,
                ctr=agg.ctr,
                average_position=agg.average_position,
                previous_clicks=agg.compare_clicks,
                previous_impressions=agg.compare_impressions,
                change_metrics=change,
                query_classification=classification,
                page_associations=pages,
                evidence={**evidence, "position_range": [t.near_page_one_min, t.near_page_one_max]},
                explanation=(
                    f'Average position {agg.average_position:.1f} is in the near-page-one heuristic range '
                    f"({t.near_page_one_min}-{t.near_page_one_max})."
                ),
                dedupe_key=f"KW_OPP_NEAR_PAGE_ONE|{agg.normalized_query}",
            )
        )

    if t.page_two_min_position < agg.average_position <= t.page_two_max_position:
        out.append(
            KeywordOpportunityDraft(
                rule_id="KW_OPP_PAGE_TWO",
                opportunity_type="page_two",
                query=agg.query,
                normalized_query=agg.normalized_query,
                priority="low",
                priority_score=score,
                clicks=agg.clicks,
                impressions=agg.impressions,
                ctr=agg.ctr,
                average_position=agg.average_position,
                change_metrics=change,
                query_classification=classification,
                page_associations=pages,
                evidence=evidence,
                explanation=f"Average position {agg.average_position:.1f} suggests page-two visibility.",
                dedupe_key=f"KW_OPP_PAGE_TWO|{agg.normalized_query}",
            )
        )

    if agg.impressions >= t.strong_impressions and agg.clicks <= t.near_zero_clicks_max:
        out.append(
            KeywordOpportunityDraft(
                rule_id="KW_OPP_HIGH_IMPRESSION_LOW_CLICK",
                opportunity_type="high_impression_low_click",
                query=agg.query,
                normalized_query=agg.normalized_query,
                priority="medium",
                priority_score=score,
                clicks=agg.clicks,
                impressions=agg.impressions,
                ctr=agg.ctr,
                average_position=agg.average_position,
                change_metrics=change,
                query_classification=classification,
                page_associations=pages,
                evidence=evidence,
                explanation=f"{int(agg.impressions)} impressions with only {int(agg.clicks)} clicks observed.",
                dedupe_key=f"KW_OPP_HIGH_IMPRESSION_LOW_CLICK|{agg.normalized_query}",
            )
        )

    if agg.average_position <= t.strong_position_max and agg.ctr < t.low_ctr:
        out.append(
            KeywordOpportunityDraft(
                rule_id="KW_OPP_STRONG_POSITION_WEAK_CTR",
                opportunity_type="strong_position_weak_ctr",
                query=agg.query,
                normalized_query=agg.normalized_query,
                priority="low",
                priority_score=score,
                clicks=agg.clicks,
                impressions=agg.impressions,
                ctr=agg.ctr,
                average_position=agg.average_position,
                change_metrics=change,
                query_classification=classification,
                page_associations=pages,
                evidence=evidence,
                explanation=f"Average position {agg.average_position:.1f} with unexpectedly low CTR {agg.ctr:.2%}.",
                dedupe_key=f"KW_OPP_STRONG_POSITION_WEAK_CTR|{agg.normalized_query}",
            )
        )

    imp_change = change["impressions"].get("change_pct")
    if imp_change is not None and imp_change >= t.growth_pct:
        out.append(
            KeywordOpportunityDraft(
                rule_id="KW_OPP_GROWING_IMPRESSIONS",
                opportunity_type="growing_impressions",
                query=agg.query,
                normalized_query=agg.normalized_query,
                priority="low",
                priority_score=score,
                clicks=agg.clicks,
                impressions=agg.impressions,
                ctr=agg.ctr,
                average_position=agg.average_position,
                previous_impressions=agg.compare_impressions,
                change_metrics=change,
                query_classification=classification,
                page_associations=pages,
                evidence={**evidence, "impressions_change_pct": imp_change},
                explanation=f"Impressions increased {imp_change:.1f}% vs previous period.",
                dedupe_key=f"KW_OPP_GROWING_IMPRESSIONS|{agg.normalized_query}",
            )
        )

    click_change = change["clicks"].get("change_pct")
    if click_change is not None and click_change >= t.growth_pct:
        out.append(
            KeywordOpportunityDraft(
                rule_id="KW_OPP_GROWING_CLICKS",
                opportunity_type="growing_clicks",
                query=agg.query,
                normalized_query=agg.normalized_query,
                priority="low",
                priority_score=score,
                clicks=agg.clicks,
                impressions=agg.impressions,
                ctr=agg.ctr,
                average_position=agg.average_position,
                previous_clicks=agg.compare_clicks,
                change_metrics=change,
                query_classification=classification,
                page_associations=pages,
                evidence={**evidence, "clicks_change_pct": click_change},
                explanation=f"Clicks increased {click_change:.1f}% vs previous period.",
                dedupe_key=f"KW_OPP_GROWING_CLICKS|{agg.normalized_query}",
            )
        )

    if click_change is not None and click_change <= t.decline_pct and agg.clicks >= t.near_zero_clicks_max:
        out.append(
            KeywordOpportunityDraft(
                rule_id="KW_OPP_DECLINING_CLICKS",
                opportunity_type="declining_clicks",
                query=agg.query,
                normalized_query=agg.normalized_query,
                priority="medium",
                priority_score=score,
                clicks=agg.clicks,
                impressions=agg.impressions,
                ctr=agg.ctr,
                average_position=agg.average_position,
                previous_clicks=agg.compare_clicks,
                change_metrics=change,
                query_classification=classification,
                page_associations=pages,
                evidence=evidence,
                explanation=f"Clicks declined {click_change:.1f}% — review recommended.",
                dedupe_key=f"KW_OPP_DECLINING_CLICKS|{agg.normalized_query}",
            )
        )

    if imp_change is not None and imp_change <= t.decline_pct:
        out.append(
            KeywordOpportunityDraft(
                rule_id="KW_OPP_DECLINING_IMPRESSIONS",
                opportunity_type="declining_impressions",
                query=agg.query,
                normalized_query=agg.normalized_query,
                priority="medium",
                priority_score=score,
                clicks=agg.clicks,
                impressions=agg.impressions,
                ctr=agg.ctr,
                average_position=agg.average_position,
                previous_impressions=agg.compare_impressions,
                change_metrics=change,
                query_classification=classification,
                page_associations=pages,
                evidence=evidence,
                explanation=f"Impressions declined {imp_change:.1f}% — review recommended.",
                dedupe_key=f"KW_OPP_DECLINING_IMPRESSIONS|{agg.normalized_query}",
            )
        )

    if len(pages) >= t.multi_page_min_pages:
        out.append(
            KeywordOpportunityDraft(
                rule_id="KW_OPP_MULTI_PAGE_RANKING",
                opportunity_type="multiple_page_ranking_signal",
                query=agg.query,
                normalized_query=agg.normalized_query,
                priority="info",
                priority_score=score,
                clicks=agg.clicks,
                impressions=agg.impressions,
                ctr=agg.ctr,
                average_position=agg.average_position,
                change_metrics=change,
                query_classification=classification,
                page_associations=pages,
                evidence={**evidence, "page_count": len(pages)},
                explanation=f"Query appears on {len(pages)} pages in Search Console data (multiple-page ranking signal).",
                dedupe_key=f"KW_OPP_MULTI_PAGE_RANKING|{agg.normalized_query}",
            )
        )

    if classification.get("long_tail") and agg.impressions >= t.min_impressions:
        out.append(
            KeywordOpportunityDraft(
                rule_id="KW_OPP_LONG_TAIL",
                opportunity_type="long_tail_query",
                query=agg.query,
                normalized_query=agg.normalized_query,
                priority="info",
                priority_score=score,
                clicks=agg.clicks,
                impressions=agg.impressions,
                ctr=agg.ctr,
                average_position=agg.average_position,
                change_metrics=change,
                query_classification=classification,
                page_associations=pages,
                evidence=evidence,
                explanation="Long-tail query (token count heuristic) with meaningful impressions.",
                dedupe_key=f"KW_OPP_LONG_TAIL|{agg.normalized_query}",
            )
        )

    return out


def _page_query_opportunities(
    qp: QueryPageRow,
    *,
    brand_terms: list[str] | None,
    thresholds: KeywordOpportunityThresholds,
) -> list[KeywordOpportunityDraft]:
    t = thresholds
    if qp.impressions < t.min_impressions:
        return []
    priority, score = compute_priority_score(
        impressions=qp.impressions, ctr=qp.ctr, average_position=qp.average_position, low_ctr=t.low_ctr
    )
    classification = _base_classification(qp.query, brand_terms, t)
    change = {
        "clicks": pct_change(qp.clicks, qp.compare_clicks),
        "impressions": pct_change(qp.impressions, qp.compare_impressions),
    }
    if qp.ctr < t.low_ctr:
        return [
            KeywordOpportunityDraft(
                rule_id="KW_OPP_QUERY_PAGE_MISMATCH",
                opportunity_type="query_page_low_ctr",
                query=qp.query,
                normalized_query=qp.normalized_query,
                page_url=qp.page_url,
                priority=priority,
                priority_score=score,
                clicks=qp.clicks,
                impressions=qp.impressions,
                ctr=qp.ctr,
                average_position=qp.average_position,
                previous_clicks=qp.compare_clicks,
                previous_impressions=qp.compare_impressions,
                change_metrics=change,
                query_classification=classification,
                page_associations=[qp.page_url],
                evidence={"data_source": "search_console_performance", "dimension": "query_page"},
                explanation=f'Query/page pair has low CTR {qp.ctr:.2%} for page {qp.page_url}.',
                dedupe_key=f"KW_OPP_QUERY_PAGE_MISMATCH|{qp.normalized_query}|{qp.page_url}",
            )
        ]
    return []
