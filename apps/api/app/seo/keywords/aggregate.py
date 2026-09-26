"""Aggregate Search Console performance rows into query-level views."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.seo.keywords.normalize import normalize_query


@dataclass
class QueryPageRow:
    query: str
    normalized_query: str
    page_url: str
    clicks: float
    impressions: float
    ctr: float
    average_position: float
    compare_clicks: float | None = None
    compare_impressions: float | None = None
    compare_ctr: float | None = None
    compare_position: float | None = None
    metrics_delta: dict[str, Any] = field(default_factory=dict)


@dataclass
class AggregatedQuery:
    query: str
    normalized_query: str
    clicks: float
    impressions: float
    ctr: float
    average_position: float
    compare_clicks: float | None = None
    compare_impressions: float | None = None
    compare_ctr: float | None = None
    compare_position: float | None = None
    pages: list[QueryPageRow] = field(default_factory=list)


def aggregate_performance_rows(rows: list[Any]) -> tuple[list[AggregatedQuery], list[QueryPageRow]]:
    """Build query-page rows and query-level aggregates from M9.3 performance rows."""
    query_pages: list[QueryPageRow] = []
    by_query: dict[str, AggregatedQuery] = {}

    for row in rows:
        if getattr(row, "dimension_type", None) == "query_page" and row.query and row.page_url:
            qp = QueryPageRow(
                query=str(row.query),
                normalized_query=normalize_query(row.query) or str(row.query).casefold(),
                page_url=str(row.page_url),
                clicks=float(row.clicks or 0),
                impressions=float(row.impressions or 0),
                ctr=float(row.ctr or 0),
                average_position=float(row.average_position or 0),
                compare_clicks=row.compare_clicks,
                compare_impressions=row.compare_impressions,
                compare_ctr=row.compare_ctr,
                compare_position=row.compare_position,
                metrics_delta=dict(row.metrics_delta or {}),
            )
            query_pages.append(qp)
            agg = by_query.get(qp.normalized_query)
            if not agg:
                agg = AggregatedQuery(
                    query=qp.query,
                    normalized_query=qp.normalized_query,
                    clicks=0,
                    impressions=0,
                    ctr=0,
                    average_position=0,
                    compare_clicks=0 if qp.compare_clicks is not None else None,
                    compare_impressions=0 if qp.compare_impressions is not None else None,
                    compare_ctr=None,
                    compare_position=None,
                    pages=[],
                )
                by_query[qp.normalized_query] = agg
            agg.clicks += qp.clicks
            agg.impressions += qp.impressions
            if qp.compare_clicks is not None:
                agg.compare_clicks = (agg.compare_clicks or 0) + qp.compare_clicks
            if qp.compare_impressions is not None:
                agg.compare_impressions = (agg.compare_impressions or 0) + qp.compare_impressions
            agg.pages.append(qp)

        elif getattr(row, "dimension_type", None) == "query" and row.query:
            norm = normalize_query(row.query) or str(row.query).casefold()
            if norm not in by_query:
                by_query[norm] = AggregatedQuery(
                    query=str(row.query),
                    normalized_query=norm,
                    clicks=float(row.clicks or 0),
                    impressions=float(row.impressions or 0),
                    ctr=float(row.ctr or 0),
                    average_position=float(row.average_position or 0),
                    compare_clicks=row.compare_clicks,
                    compare_impressions=row.compare_impressions,
                    compare_ctr=row.compare_ctr,
                    compare_position=row.compare_position,
                    pages=[],
                )

    for agg in by_query.values():
        if agg.impressions > 0:
            agg.ctr = agg.clicks / agg.impressions
        if agg.pages:
            total_imp = sum(p.impressions for p in agg.pages) or 1
            agg.average_position = sum(p.average_position * p.impressions for p in agg.pages) / total_imp
            if any(p.compare_position is not None for p in agg.pages):
                agg.compare_position = sum(
                    (p.compare_position or p.average_position) * p.impressions for p in agg.pages
                ) / total_imp
        if agg.compare_clicks is not None and agg.compare_impressions:
            agg.compare_ctr = agg.compare_clicks / agg.compare_impressions if agg.compare_impressions else 0

    return list(by_query.values()), query_pages
