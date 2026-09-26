# M9.3 — Search Console Intelligence

## Overview

Read-only Search Console intelligence built on the existing Google Search Console OAuth integration. Transforms GSC API data into tenant-scoped performance snapshots and deterministic opportunities.

```
Google Search Console API → Sync → Performance rows → Opportunity detection → API + UI
```

## Important limitations

- Based on **read-only** Search Console API data only
- Does **not** guarantee indexing, ranking, or traffic outcomes
- Recent days may be incomplete (3-day latency adjustment applied)
- Country/device/search appearance dimensions not synced in M9.3 (query, page, query+page supported)
- No AI recommendations in this milestone

## OAuth

Reuses existing `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` and `ensure_access_token`. Scope: `webmasters.readonly`.

Connect via Integrations UI, then use Search Console intelligence endpoints.

## Supported date ranges

| Preset | Description |
|--------|-------------|
| `last_7_days` | 7 days ending ~3 days ago |
| `last_28_days` | 28 days ending ~3 days ago |
| `last_90_days` | 90 days ending ~3 days ago |
| `custom` | Up to 90 days (requires start/end dates) |

Effective end date is adjusted for Search Console reporting latency.

## Sync behavior

- Idempotent via `(organization_id, sync_key)` unique constraint
- Fetches aggregate totals + query/page/query_page dimensions
- Optional previous-period comparison (equivalent length)
- Rate limited: `gsc_sync` per organization
- Max rows per dimension: configurable (default 500)

## Opportunity heuristics

| Rule ID | Signal |
|---------|--------|
| `GSC_OPP_LOW_CTR_QUERY` | Impressions ≥ 100, CTR below 2% |
| `GSC_OPP_LOW_CTR_PAGE` | Page impressions ≥ 100, low CTR |
| `GSC_OPP_PAGE_BOUNDARY` | Position 8–15 with impressions |
| `GSC_OPP_STRONG_IMPRESSIONS_WEAK_CLICKS` | ≥ 500 impressions, weak clicks |
| `GSC_OPP_STRONG_POSITION_WEAK_CTR` | Position ≤ 10, CTR below threshold |
| `GSC_OPP_DECLINING_CLICKS_PAGE` | Clicks down ≥ 20% vs prior period |
| `GSC_OPP_DECLINING_IMPRESSIONS_PAGE` | Impressions down ≥ 20% |
| `GSC_OPP_QUERY_PAGE_COMBO` | High-impression query/page with signals |

Thresholds are documented in `app/seo/search_console/thresholds.py`.

## API endpoints

| Method | Path |
|--------|------|
| GET | `/api/v1/seo/search-console/properties` |
| POST | `/api/v1/seo/search-console/sync` |
| GET | `/api/v1/seo/search-console/sync/{sync_id}` |
| GET | `/api/v1/seo/search-console/performance` |
| GET | `/api/v1/seo/search-console/queries` |
| GET | `/api/v1/seo/search-console/pages` |
| GET | `/api/v1/seo/search-console/opportunities` |
| GET | `/api/v1/seo/search-console/summary` |

All endpoints require authentication and are tenant-scoped.

## Security

- OAuth tokens encrypted at rest; never returned via API
- No token logging
- Bounded pagination (max 500)
- Org-scoped sync rate limit
- External query/page strings treated as untrusted in UI

## Future milestones

- M9.7: AI recommendations from opportunities
- M9.14: Full SEO dashboard
