# Analytics Ingestion Foundation

## Architecture

GrowthOS ingests advertising performance from connected Meta and Google Ads
integrations into a **provider-neutral** table: `marketing_performance_daily`.

```
POST /analytics/ingest  (enqueue)
        │
        ▼
JobQueue job type: analytics.ingest
        │
        ▼
AnalyticsIngestionService.ingest()
        │
        ├── MetaInsightsFetcher     (Graph insights, read-only)
        └── GoogleAdsInsightsFetcher (GAQL search, read-only)
                │
                ▼
        NormalizedPerformanceRow
                │
                ▼
        idempotent upsert → marketing_performance_daily
                │
                ▼
GET /analytics/performance  (org-scoped read API)
```

Existing `analytics.sync` / `AnalyticsDaily` / `AnalyticsCampaign` paths remain
unchanged. This milestone adds the normalized foundation for later AI
intelligence and autonomous optimization — it does **not** replace legacy
sync writers yet.

## Normalized metrics

| Field | Meaning |
|-------|---------|
| impressions, reach, clicks | Traffic |
| spend | Media cost (currency column) |
| conversions, leads | Outcomes (provider-mapped) |
| revenue | Conversion value when available |
| ctr, cpc, cpm, cpl, cpa, roas | Derived; `null` when denominator is zero |

Natural key (unique):

`organization_id + platform + entity_level + external_account_id +
external_campaign_id + external_ad_set_id + external_ad_id + date + granularity`

Missing external ids are stored as empty strings for portable uniqueness.

`provider_metadata` is sanitized — never access tokens, refresh tokens, or
authorization headers.

## Ingestion jobs

| Job type | Purpose |
|----------|---------|
| `analytics.ingest` | Normalized Meta/Google Ads performance upsert |
| `analytics.sync` | Legacy integration sync (unchanged) |

Dedupe key:

`analytics-ingest:{org}:{provider}:{client|org}:{YYYY-MM-DD}:lb{lookback}`

Retry policy:
- **Retryable:** timeout, transport, rate limit
- **Unrecoverable:** missing/disconnected credentials, unsupported provider/operation, malformed permanent config

Audit actions (no secrets):
- `analytics.ingestion_enqueued`
- `analytics.ingestion_started`
- `analytics.ingestion_completed`
- `analytics.ingestion_failed`

## Configuration

| Env | Default | Notes |
|-----|---------|-------|
| `ANALYTICS_INGESTION_ENABLED` | `true` | Emergency latch |
| `ANALYTICS_INGESTION_LOOKBACK_DAYS` | `7` | Default window |
| `ANALYTICS_INGESTION_MAX_LOOKBACK_DAYS` | `30` | Hard ceiling (no unlimited crawl) |
| `ANALYTICS_INGESTION_BATCH_SIZE` | `500` | Upsert flush size |

## Supported providers

| Provider | Entity levels | Notes |
|----------|---------------|-------|
| Meta | campaign (default); adset/ad when requested | Graph `/insights` daily |
| Google Ads | campaign | GAQL `segments.date` daily |
| Google Ads | ad / ad_set | **Unsupported** (honest error) |
| Instagram / WhatsApp / GA4 / YouTube | — | Not part of this milestone |

## APIs

- `POST /api/v1/analytics/ingest` — enqueue job (`integration_connect` permission)
- `GET /api/v1/analytics/performance` — list normalized rows with org/client/platform/date/campaign filters + pagination

No dashboard UI in this milestone.

## Failure behavior

- Failed ingestion is never marked successful.
- Ambiguous network failures raise retryable errors; JobQueue applies backoff.
- Disconnected integrations fail closed with `INTEGRATION_DISCONNECTED`.
- Provider responses are sanitized before persistence and audit.

## Limitations

- Live Meta/Google ingestion is **not** production-verified without credentials/sandbox.
- Google ad/ad_set grain not implemented.
- Incremental watermark cursor is date-window based (lookback), not per-entity high-water marks.
- Legacy `AnalyticsDaily` duplicate-on-resync behavior is unchanged.
- SEO out of scope.
