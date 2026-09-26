# SEO Topic Clustering (M9.5)

## Objective

Group Search Console queries into deterministic topical clusters using persisted M9.4 keyword opportunity data and M9.3 performance rows. Answers:

- Which search queries appear to belong to the same underlying topic?
- Which pages currently represent those topics?

No LLM, embeddings, or external AI providers are used.

## Architecture

```
M9.1 crawler → M9.2 technical findings → M9.3 Search Console → M9.4 keyword opportunities → M9.5 topic clustering
```

Input is **persisted database data only** — clustering does not call Google Search Console.

## Input data

1. `SearchConsolePerformanceRow` (`query`, `query_page` dimensions) for metrics and page URLs
2. `KeywordOpportunity` for opportunity counts per normalized query

Keyword analysis (M9.4) must run before topic analysis.

## Normalization

Reuses M9.4 `normalize_query()`:

- Unicode NFKC normalization
- Trim and collapse whitespace
- Casefold for matching

Original query text is preserved; normalized text drives clustering.

## Clustering algorithm (v1)

1. Load eligible performance rows for the sync (capped at 2,000 queries by impressions)
2. Deduplicate by normalized query; aggregate clicks/impressions/page metrics
3. Tokenize each query (Unicode word tokens; optional conservative stop-word removal disabled by default)
4. Build inverted token index
5. For each query, compare Jaccard similarity only with candidate queries sharing ≥1 token (max 200 candidates)
6. Union-find merges pairs with Jaccard ≥ threshold
7. Emit connected components as clusters; isolated queries become singleton clusters

## Similarity calculation

**Jaccard similarity** over token sets:

```
similarity(A, B) = |tokens(A) ∩ tokens(B)| / |tokens(A) ∪ tokens(B)|
```

Membership `similarity_score` is Jaccard vs the cluster representative query tokens.

## Thresholds

| Parameter | Default | Purpose |
|-----------|---------|---------|
| `min_jaccard_similarity` | 0.45 | Minimum pair similarity to merge |
| `min_shared_tokens` | 1 | Candidate generation gate |
| `max_queries_per_analysis` | 2000 | Server-enforced workload cap |
| `max_candidates_per_query` | 200 | Pairwise comparison cap |

## Singleton handling

Queries with no sufficient match remain as **singleton clusters** (`is_singleton=true`). They are persisted, not discarded.

## Topic labeling

Deterministic — no LLM:

1. If cluster has one query → use that query
2. Else → up to four most frequent shared tokens (≥2 queries), sorted by frequency then alphabetically
3. Fallback → representative query (highest impressions)

## Performance aggregation

Per cluster:

- `total_clicks`, `total_impressions` — sum across member queries
- `aggregate_ctr` — `total_clicks / total_impressions` (0 if no impressions)
- `weighted_average_position` — impression-weighted mean of query average positions
- `opportunity_count` — sum of M9.4 opportunity rows per query

## Page association

Pages come from `query_page` performance rows. Per URL within a cluster:

- Sum clicks/impressions across contributing query-page rows
- CTR from aggregated clicks/impressions
- Weighted average position from impression-weighted sums
- Highest-impression URL marked `is_primary`

## Multi-page signal

When a cluster has ≥2 distinct URLs with impressions, `multi_page_signal=true`. This is a **neutral factual signal** — not labeled as cannibalization.

## Algorithm version

Stored as `algorithm_version=v1` on every cluster. Re-analysis replaces clusters for the sync (idempotent by `sync_id` + `cluster_key`).

## Idempotency

Re-running analysis on the same sync deletes prior clusters for that sync and recreates them. Stable `cluster_key` = SHA-256 of sorted normalized queries + algorithm version.

## APIs

| Method | Path | Description |
|--------|------|-------------|
| POST | `/api/v1/seo/topics/analyze` | Run clustering (rate limited) |
| GET | `/api/v1/seo/topics` | List clusters (paginated, sort allowlist) |
| GET | `/api/v1/seo/topics/summary` | Aggregate summary |
| GET | `/api/v1/seo/topics/{id}` | Cluster detail |
| GET | `/api/v1/seo/topics/{id}/queries` | Member queries |
| GET | `/api/v1/seo/topics/{id}/pages` | Associated pages |

## Security

- Authentication required on all endpoints
- Organization scoping on every query
- Rate limit: `topic_analyze` per organization per hour
- Bounded pagination (max 500)
- Allowlisted sort fields
- No external fetch / SSRF during clustering
- Query text and URLs treated as untrusted in API responses and UI

## Resource limits

- Max 2,000 queries per analysis
- Token inverted index limits pairwise comparisons
- Max 200 candidates per query

## Limitations

- Lexical similarity only — synonyms may not cluster
- English-agnostic tokenization; no language-specific stemming
- Requires prior M9.4 keyword analysis
- Average position is aggregated, not an exact rank

## M9.6 handoff

Each cluster exposes:

- `topic_label`, `representative_query`
- Associated queries with metrics and similarity scores
- Associated pages with per-URL performance
- `multi_page_signal`, `opportunity_count`
- Aggregate performance metrics

M9.6 can use this for competitor/content-gap analysis without re-clustering.
