# AI Performance Intelligence (Milestone 2)

## Architecture

```
MarketingPerformanceDaily
        │
        ▼
AnalysisWindow (7 / 14 / 30 vs prior equal window)
        │
        ▼
EntityPeriodComparison + derived metrics
        │
        ▼
Signal detection (threshold + sample-size gated)
        │
        ▼
Confidence heuristic (transparent, non-statistical)
        │
        ├── Deterministic explanation (always available)
        └── Optional AI explanation (evidence-bound; hallucinated numbers rejected)
                │
                ▼
performance_recommendations  (analysis-only lifecycle)
                │
                ▼
GET /analytics/recommendations
```

**ANALYSIS ≠ RECOMMENDATION ≠ EXECUTION**

- Analysis computes comparisons and signals from stored performance.
- Recommendations persist structured advice with informational `suggested_action`.
- Execution (Meta/Google mutations via `AIAction`) is **out of scope** for this milestone.
  Approving a recommendation does **not** pause campaigns, change budgets, or call providers.

## Supported metrics

Volume: impressions, reach, clicks, spend, conversions, leads, revenue  
Rates: CTR, CPC, CPM, CPL, CPA, ROAS (null when denominator is zero)

## Comparison windows

| Window | Current | Previous |
|--------|---------|----------|
| 7 | last 7 days | prior 7 days |
| 14 | last 14 days | prior 14 days |
| 30 | last 30 days | prior 30 days |

## Signal categories

| Category | Examples |
|----------|----------|
| UNDERPERFORMANCE | CPL/CPA↑, ROAS↓, CTR↓, CPC↑, conversions↓ |
| POSITIVE | ROAS↑, CPL↓, CTR↑, conversions↑ |
| EFFICIENCY | high spend / poor CPL vs account; low spend / strong ROAS |
| TREND | sudden large swing (`PERFORMANCE_SUDDEN_CHANGE_PERCENT`) |

Slight changes below `PERFORMANCE_SIGNIFICANT_CHANGE_PERCENT` do not emit signals.
Insufficient sample size emits no recommendations.

## Threshold configuration

| Env | Default |
|-----|---------|
| `PERFORMANCE_MIN_SPEND` | 50 |
| `PERFORMANCE_MIN_IMPRESSIONS` | 1000 |
| `PERFORMANCE_MIN_CLICKS` | 20 |
| `PERFORMANCE_MIN_CONVERSIONS` | 1 |
| `PERFORMANCE_SIGNIFICANT_CHANGE_PERCENT` | 20 |
| `PERFORMANCE_SUDDEN_CHANGE_PERCENT` | 50 |
| `PERFORMANCE_MIN_DAYS_WITH_DATA` | 3 |
| `PERFORMANCE_RECOMMENDATION_TTL_DAYS` | 14 |

Validated at startup via `validate_performance_intelligence_settings`.

## Confidence methodology

Heuristic score in `[0.05, 0.99]`, **not** statistical significance:

1. Base 0.35  
2. + up to 0.25 for days-with-data completeness in both windows  
3. + up to 0.30 for volume (impressions/clicks/conversions/spend vs mins)  
4. + up to 0.20 for change magnitude relative to significance threshold  
5. − penalties when rate metrics lack adequate clicks/conversions  

## Recommendation lifecycle

`NEW → REVIEWED → APPROVED | REJECTED` and `EXPIRED` (TTL).

`APPROVED` is a human review state only — **no external execution**.

Idempotency: unique `(organization_id, fingerprint)` per entity + type + metric + window end.

## AI explanation behavior

- Deterministic evidence is the source of truth.
- LLM receives structured evidence JSON and must not invent numbers.
- Numeric claims outside the evidence set are rejected → deterministic fallback.
- Provider failures / empty responses → deterministic fallback.
- Feature works with `AI_PROVIDER=mock` offline; no live AI required for basic recommendations.

## APIs

- `POST /api/v1/analytics/analyze` — enqueue `analytics.analyze`
- `GET /api/v1/analytics/recommendations` — filtered list + pagination
- `GET /api/v1/analytics/recommendations/{id}`
- `PATCH /api/v1/analytics/recommendations/{id}` — lifecycle only

## Job

`analytics.analyze` — tenant-scoped, deduped per org/client/platform/window/day.

Audit: `analytics.analysis.started|completed|failed`, `analytics.recommendation.created`, `analytics.recommendation.status_changed`.

## Security

All queries scoped by `organization_id`. Client must belong to org. Job handler asserts tenant ownership. No tokens/secrets in audits.

## Limitations

- Not live-provider verified
- Not statistically validated recommendations
- Not autonomous optimization / execution
- Campaign grain default; ad/ad_set analysis depends on ingested entity_level rows
- Account-average efficiency signals require multiple entities in the same analysis run
