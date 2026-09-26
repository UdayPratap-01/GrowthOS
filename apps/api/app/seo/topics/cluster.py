"""Deterministic topic clustering over Search Console queries."""

from __future__ import annotations

import hashlib
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

from app.seo.keywords.normalize import normalize_query
from app.seo.topics.similarity import jaccard_similarity
from app.seo.topics.thresholds import ALGORITHM_VERSION, DEFAULT_TOPIC_THRESHOLDS, TopicClusterThresholds
from app.seo.topics.tokens import tokenize_query


@dataclass
class PageMetrics:
    clicks: float = 0.0
    impressions: float = 0.0
    position_weighted_sum: float = 0.0


@dataclass
class QueryRecord:
    query: str
    normalized_query: str
    tokens: set[str]
    clicks: float
    impressions: float
    ctr: float
    average_position: float
    pages: set[str] = field(default_factory=set)
    page_metrics: dict[str, PageMetrics] = field(default_factory=dict)
    opportunity_count: int = 0


@dataclass
class ClusterDraft:
    cluster_key: str
    topic_label: str
    representative_query: str
    queries: list[QueryRecord]
    is_singleton: bool
    multi_page_signal: bool
    total_clicks: float
    total_impressions: float
    aggregate_ctr: float
    weighted_average_position: float
    opportunity_count: int
    page_count: int
    pages: list[dict[str, Any]]


class UnionFind:
    def __init__(self, n: int) -> None:
        self.parent = list(range(n))

    def find(self, x: int) -> int:
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, a: int, b: int) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[rb] = ra


def build_query_records(rows: list[dict[str, Any]]) -> list[QueryRecord]:
    by_norm: dict[str, QueryRecord] = {}
    for row in rows:
        norm = row.get("normalized_query") or normalize_query(row.get("query"))
        if not norm:
            continue
        rec = by_norm.get(norm)
        if not rec:
            rec = QueryRecord(
                query=str(row.get("query") or norm),
                normalized_query=norm,
                tokens=tokenize_query(norm),
                clicks=0.0,
                impressions=0.0,
                ctr=0.0,
                average_position=0.0,
            )
            by_norm[norm] = rec
        clicks = float(row.get("clicks") or 0)
        impressions = float(row.get("impressions") or 0)
        position = float(row.get("average_position") or 0)
        rec.clicks += clicks
        rec.impressions += impressions
        rec.opportunity_count += int(row.get("opportunity_count") or 0)
        page_url = row.get("page_url")
        if page_url:
            url = str(page_url)
            rec.pages.add(url)
            pm = rec.page_metrics.setdefault(url, PageMetrics())
            pm.clicks += clicks
            pm.impressions += impressions
            pm.position_weighted_sum += position * impressions
    for rec in by_norm.values():
        if rec.impressions > 0:
            rec.ctr = rec.clicks / rec.impressions
            rec.average_position = sum(
                pm.position_weighted_sum for pm in rec.page_metrics.values()
            ) / rec.impressions if rec.page_metrics else 0.0
        elif rec.page_metrics:
            total_imp = sum(pm.impressions for pm in rec.page_metrics.values())
            rec.average_position = (
                sum(pm.position_weighted_sum for pm in rec.page_metrics.values()) / total_imp if total_imp > 0 else 0.0
            )
    return sorted(by_norm.values(), key=lambda r: r.normalized_query)


def cluster_queries(
    records: list[QueryRecord],
    *,
    thresholds: TopicClusterThresholds = DEFAULT_TOPIC_THRESHOLDS,
) -> list[ClusterDraft]:
    if not records:
        return []
    records = records[: thresholds.max_queries_per_analysis]
    n = len(records)
    uf = UnionFind(n)

    inverted: dict[str, list[int]] = defaultdict(list)
    for i, rec in enumerate(records):
        for token in rec.tokens:
            inverted[token].append(i)

    for i, rec in enumerate(records):
        candidates: set[int] = set()
        for token in rec.tokens:
            for j in inverted[token]:
                if j != i:
                    candidates.add(j)
        for j in list(candidates)[: thresholds.max_candidates_per_query]:
            other = records[j]
            shared = len(rec.tokens & other.tokens)
            if shared < thresholds.min_shared_tokens:
                continue
            sim = jaccard_similarity(rec.tokens, other.tokens)
            if sim >= thresholds.min_jaccard_similarity:
                uf.union(i, j)

    groups: dict[int, list[int]] = defaultdict(list)
    for i in range(n):
        groups[uf.find(i)].append(i)

    drafts: list[ClusterDraft] = []
    for members in groups.values():
        qs = [records[i] for i in members]
        drafts.append(_build_cluster(qs, thresholds))
    return drafts


def _build_cluster(queries: list[QueryRecord], thresholds: TopicClusterThresholds) -> ClusterDraft:
    queries.sort(key=lambda q: (-q.impressions, q.normalized_query))
    representative = queries[0]
    is_singleton = len(queries) == 1

    total_clicks = sum(q.clicks for q in queries)
    total_impressions = sum(q.impressions for q in queries)
    aggregate_ctr = (total_clicks / total_impressions) if total_impressions > 0 else 0.0
    weighted_position = (
        sum(q.average_position * q.impressions for q in queries) / total_impressions if total_impressions > 0 else 0.0
    )

    page_stats: dict[str, PageMetrics] = defaultdict(PageMetrics)
    for q in queries:
        for url, pm in q.page_metrics.items():
            agg = page_stats[url]
            agg.clicks += pm.clicks
            agg.impressions += pm.impressions
            agg.position_weighted_sum += pm.position_weighted_sum

    pages = []
    for url, stats in sorted(page_stats.items(), key=lambda x: -x[1].impressions):
        imp = stats.impressions
        pages.append(
            {
                "page_url": url,
                "clicks": round(stats.clicks, 2),
                "impressions": round(imp, 2),
                "ctr": round(stats.clicks / imp, 4) if imp > 0 else 0.0,
                "average_position": round(stats.position_weighted_sum / imp, 2) if imp > 0 else 0.0,
            }
        )

    topic_label = _topic_label(queries)
    norms = sorted(q.normalized_query for q in queries)
    cluster_key = hashlib.sha256(f"{ALGORITHM_VERSION}|{'|'.join(norms)}".encode()).hexdigest()[:64]

    return ClusterDraft(
        cluster_key=cluster_key,
        topic_label=topic_label,
        representative_query=representative.query,
        queries=queries,
        is_singleton=is_singleton,
        multi_page_signal=len(pages) >= 2,
        total_clicks=total_clicks,
        total_impressions=total_impressions,
        aggregate_ctr=aggregate_ctr,
        weighted_average_position=weighted_position,
        opportunity_count=sum(q.opportunity_count for q in queries),
        page_count=len(pages),
        pages=pages,
    )


def _topic_label(queries: list[QueryRecord]) -> str:
    """Deterministic label from shared tokens or representative query."""
    if len(queries) == 1:
        return queries[0].query
    token_counts: dict[str, int] = defaultdict(int)
    for q in queries:
        for t in q.tokens:
            token_counts[t] += 1
    shared = [t for t, c in token_counts.items() if c >= 2]
    if shared:
        shared.sort(key=lambda t: (-token_counts[t], t))
        return " ".join(shared[:4])
    return queries[0].query
