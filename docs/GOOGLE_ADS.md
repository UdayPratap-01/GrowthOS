# Google Ads Integration (M7 / M9.18)

## API version

**Current:** Google Ads REST API **v25**  
**Verified:** 2026-09-28  
**Source:** [Google Ads API sunset dates](https://developers.google.com/google-ads/api/docs/sunset-dates)

Central constant: `app/integrations/google_ads_api.py` → `GOOGLE_ADS_API_VERSION`.

Previous v18 usage was removed in M9.18 (v18 sunset 2025-08-20).

## Authentication (Sept 2026 model)

| Requirement | Required for OAuth connect | Required for API calls |
|-------------|-------------------------|------------------------|
| `GOOGLE_CLIENT_ID` | Yes | Yes (via stored OAuth tokens) |
| `GOOGLE_CLIENT_SECRET` | Yes | Yes (refresh) |
| `GOOGLE_ADS_DEVELOPER_TOKEN` | **No** (optional) | **No** (optional; sent if configured) |
| Google Cloud project API access level | No at connect | Yes for production accounts |
| `GOOGLE_ADS_LOGIN_CUSTOMER_ID` | No | Optional (MCC) |

Developer tokens were [sunset 2026-09-09](https://developers.google.com/google-ads/api/docs/api-policy/developer-token). API access levels are determined by the **Google Cloud project** used for OAuth. Production accounts may return `CLOUD_PROJECT_NOT_APPROVED_FOR_PRODUCTION` until the Cloud project is approved in Google Cloud Console → Google Ads API Overview.

## Supported GrowthOS capabilities

| Capability | Status |
|------------|--------|
| OAuth connect/disconnect | Supported |
| Customer discovery | Supported |
| Campaign metrics sync (7d) | Supported (requires `client_id`) |
| Analytics ingestion (GAQL) | Supported |
| Campaign pause/resume | Supported — gated by canary/autonomous |
| Budget update | **Unsupported** |
| Campaign/ad create | **Unsupported** |
| Provider verification | Read-only; `safe_for_mutation` always false |

## Execution architecture

All mutations flow through:

```
ActionService (approval) → ExecutionEngine → AdsExecutor → Google Ads API v25 → AdsReconciliation
```

Live execution defaults **OFF**. Requires explicit canary configuration and operator enablement.

## Environment variables

```
GOOGLE_CLIENT_ID=
GOOGLE_CLIENT_SECRET=
GOOGLE_ADS_REDIRECT_URI=          # optional
GOOGLE_ADS_LOGIN_CUSTOMER_ID=     # optional MCC
GOOGLE_ADS_DEVELOPER_TOKEN=       # optional since Sept 2026
```

## Tests

- `tests/test_google_ads_api.py` — M9.18 version/auth tests
- `tests/test_google_m7_verification.py` — discovery, pause/resume, reconciliation (mocked)
