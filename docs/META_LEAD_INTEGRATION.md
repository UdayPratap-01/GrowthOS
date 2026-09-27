# Meta Lead Integration (M9.19)

**Graph API version:** `v25.0` (verified 2026-09-28)  
**Source:** [Graph API Versions](https://developers.facebook.com/docs/graph-api/changelog/versions/)

---

## Overview

GrowthOS ingests Meta Lead Ads via:

1. **OAuth connect** — discovers Facebook Pages and stores encrypted Page access tokens
2. **Webhook** — receives `leadgen` events, verifies HMAC signature, deduplicates, routes by `page_id`
3. **Graph enrichment** — fetches `field_data` using the Page access token (`leads_retrieval`)

Lead normalization into GrowthOS `Lead` records is unchanged. Contact details are never invented.

---

## Implemented in code

| Capability | Status |
|------------|--------|
| Centralized Graph API `v25.0` | Implemented |
| Page discovery at OAuth (`/me/accounts`) | Implemented |
| `page_id` / `page_ids[]` persistence in `Integration.config` | Implemented (no migration) |
| Encrypted Page access tokens in `Integration.secret_ref` | Implemented |
| Multi-Page: all Pages stored; no silent single-Page pick | Implemented |
| Lead fetch via current Graph API | Implemented |
| Webhook HMAC-SHA256 verification | Implemented |
| Webhook verification challenge (`GET /webhooks/meta`) | Implemented |
| Idempotency via `WebhookEvent` unique `(provider, event_id)` | Implemented |
| Tenant routing by persisted `page_id` / `page_ids` | Implemented |
| Cross-tenant conflict → quarantine (`ambiguous`) | Implemented |
| Connect-time `PAGE_ALREADY_CONNECTED` guard | Implemented |

---

## Requires Meta / provider approval

These cannot be verified locally without Meta App Review and production credentials:

| Requirement | Purpose |
|-------------|---------|
| `leads_retrieval` (Advanced Access) | Read lead form field data |
| `pages_show_list`, `pages_read_engagement` | Page discovery |
| `ads_read`, `ads_management`, `business_management` | Ads sync (M6) |
| Meta App Review | Production OAuth scopes |
| Page role on connected Facebook Page | Page token issuance |

GrowthOS requests lead scopes at OAuth but **does not claim** they are granted until Meta approves the app.

---

## Requires production infrastructure

| Requirement | Notes |
|-------------|-------|
| Public HTTPS webhook URL | Meta cannot deliver webhooks to localhost |
| `META_WEBHOOK_VERIFY_TOKEN` | Challenge verification on `GET /webhooks/meta` |
| `META_APP_SECRET` | Signature validation on `POST /webhooks/meta` |
| Webhook subscription in Meta App Dashboard | Subscribe Page object to `leadgen` field |

**Production webhook readiness:** Code is implemented and tested with mocked signatures. Live delivery requires deployment — not claimed as production-ready without a public endpoint.

---

## OAuth flow

1. Operator initiates connect at `/api/v1/integrations/meta/connect`
2. HMAC-signed state binds `organization_id`, `client_id`, `user_id` (900s TTL)
3. Callback exchanges code → long-lived user token
4. Discovers ad accounts (M6) and Facebook Pages (`/me/accounts`)
5. Persists sanitized config (no tokens in JSON):

```json
{
  "page_id": "<set only when exactly one Page>",
  "page_ids": ["..."],
  "pages": [{"id": "...", "name": "...", "tasks": []}],
  "lead_ads": {
    "pages_connected": 1,
    "page_selection_required": false,
    "webhook_routing_ready": true
  }
}
```

6. Encrypted token payload includes `page_access_tokens` map keyed by Page ID

### Multiple Pages

When more than one Page is accessible:

- All Page IDs are stored in `page_ids[]`
- `page_id` is **not** set (no arbitrary selection)
- Webhooks route correctly when `page_id` in the event matches any entry in `page_ids[]`
- Operator UI does not yet provide Page picker — documented limitation

---

## Webhook setup

1. Set `META_APP_SECRET` and `META_WEBHOOK_VERIFY_TOKEN`
2. Register callback: `{API_PUBLIC_URL}/api/v1/webhooks/meta`
3. Subscribe Page object → `leadgen` field
4. Ensure connected integration includes the Page ID in `page_ids`

### Security

- Invalid signature → `401`
- Malformed payload → `400` (no retry)
- Processing failure → `500` (Meta retries)
- Duplicate `leadgen_id` → ignored (idempotent)
- Ambiguous Page ownership → quarantined, no lead created

---

## Environment variables

| Variable | Required | Purpose |
|----------|----------|---------|
| `META_APP_ID` | Yes (connect) | OAuth client ID |
| `META_APP_SECRET` | Yes | OAuth + webhook signature |
| `META_REDIRECT_URI` | Optional | Default: `{API_PUBLIC_URL}/api/v1/integrations/meta/callback` |
| `META_WEBHOOK_VERIFY_TOKEN` | Yes (webhook verify) | Challenge token |

Never commit real secrets. Tokens are stored encrypted server-side only.

---

## Troubleshooting

| Symptom | Likely cause |
|---------|--------------|
| Leads `unroutable` | Page not in integration `page_ids` — reconnect Meta OAuth |
| `ambiguous` webhook events | Same Page connected to multiple tenants — disconnect duplicate |
| `PAGE_ALREADY_CONNECTED` on connect | Page claimed by another integration |
| Enrichment `unavailable` | Missing `leads_retrieval` or expired Page token |
| Webhook verify fails | `META_WEBHOOK_VERIFY_TOKEN` mismatch |
| Signature 401 | `META_APP_SECRET` mismatch |

---

## M6 ads safety

M9.19 does **not** enable autonomous Meta Ads execution. Canary, approval gates, and read-only defaults remain unchanged.
