# M9.4 — Keyword Opportunity Engine

## Purpose

Deterministic keyword opportunity detection from persisted M9.3 Search Console performance data. No Google API calls during analysis. No AI. No content generation.

## Architecture

```
M9.3 SearchConsolePerformanceRow
        ↓
Query normalization + aggregation
        ↓
Deterministic rules + priority formula
        ↓
KeywordOpportunity records
        ↓
API + /seo/keywords UI
```

## Source data

Uses `search_console_performance_rows` with `dimension_type` of `query` and `query_page`.

**Impressions are Search Console performance metrics — not keyword search volume.**

## Query normalization

- Unicode NFKC normalization
- Trim whitespace
- Collapse repeated spaces
- Casefold for matching only
- Original query preserved for display

## Opportunity rules (12)

| Rule ID | Type |
|---------|------|
| KW_OPP_HIGH_IMPRESSION_LOW_CTR | high_impression_low_ctr |
| KW_OPP_NEAR_PAGE_ONE | near_page_one |
| KW_OPP_PAGE_TWO | page_two |
| KW_OPP_HIGH_IMPRESSION_LOW_CLICK | high_impression_low_click |
| KW_OPP_STRONG_POSITION_WEAK_CTR | strong_position_weak_ctr |
| KW_OPP_GROWING_IMPRESSIONS | growing_impressions |
| KW_OPP_GROWING_CLICKS | growing_clicks |
| KW_OPP_DECLINING_CLICKS | declining_clicks |
| KW_OPP_DECLINING_IMPRESSIONS | declining_impressions |
| KW_OPP_MULTI_PAGE_RANKING | multiple_page_ranking_signal |
| KW_OPP_LONG_TAIL | long_tail_query |
| KW_OPP_QUERY_PAGE_MISMATCH | query_page_low_ctr |

## Priority formula

```
score = log10(impressions+1) * position_factor * ctr_gap_factor
position_factor = max(0.5, (21 - avg_position) / 20)
ctr_gap_factor = max(1.0, (low_ctr - ctr) / low_ctr) when ctr < low_ctr
```

Not a traffic prediction.

## Branded / non-branded

Only when `brand_terms` provided to analyze API. Otherwise `unknown`.

## API

| Method | Path |
|--------|------|
| POST | `/api/v1/seo/keywords/analyze` |
| GET | `/api/v1/seo/keywords` |
| GET | `/api/v1/seo/keywords/opportunities` |
| GET | `/api/v1/seo/keywords/summary` |
| GET | `/api/v1/seo/keywords/{id}` |
| GET | `/api/v1/seo/keywords/query/{query}` |
| GET | `/api/v1/seo/keywords/pages/{page_url}` |

## M9.5 preparation

Exposes `query`, `normalized_query`, `page_url`, `opportunity_type`, `evidence`, metrics for topic clustering.

## Limitations

- No search volume estimates
- Average position ≠ exact rank
- No LLM clustering
- Requires completed M9.3 sync first
