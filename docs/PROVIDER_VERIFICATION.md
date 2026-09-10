# Provider Verification — Phase 1 (Read-Only) + M6 Meta Lifecycle

**PROVIDER VERIFIED does not mean AUTONOMOUS SPEND ENABLED.**

Phase 1 proves GrowthOS can establish the intended **read-only** connection to Meta / Google Ads. It does **not** authorize mutations, closed-loop execution, or live spend.

Milestone 6 completes the **Meta OAuth + long-lived token + ad-account discovery** path that feeds Phase 1 verification and Phase 2 canary. Live mutations still require the canary confirm phrase and allowlists.

## Distinctions

| Status | Meaning |
|--------|---------|
| **AUTOMATED TEST VERIFICATION** | Mocked Graph API tests in CI (no real credentials) |
| **REAL META VERIFICATION** | Manual canary with live Meta App + test ad account |
| **PRODUCTION SIGN-OFF** | Explicit later approval for autonomous spend (not M6) |
| **M8 PRODUCTION DEPLOY** | Platform packaging / CI / runbooks — **not** provider live verification |

Milestone 8 prepares controlled production *deployment* of the product platform.
It does **not** flip Meta/Google live execution. Until real canaries pass:

```text
LIVE ADS = OFF
AUTONOMOUS MUTATIONS = OFF
```

See [PRODUCTION_READINESS.md](./PRODUCTION_READINESS.md) and [PRODUCTION_RUNBOOK.md](./PRODUCTION_RUNBOOK.md).

## What Phase 1 does

1. Local **preflight** (no network): app credentials present? OAuth integration connected?
2. Explicit operator confirmation
3. Read-only API checks (when connected):
   - Meta: `/me`, `/me/adaccounts`, optional campaigns list
   - Google Ads: `customers:listAccessibleCustomers`, optional campaign search
4. Structured result + audit + metrics
5. Persist sanitized snapshot on `integrations.config.last_verification` (no secrets)
6. **M6:** Persist discovered Meta `ad_accounts` / campaign hints into `integrations.config` for canary allowlists

## What Phase 1 never does

- Create / pause / resume / budget-change campaigns
- Call `ActionService` or create `AIAction`
- Enqueue execution jobs
- Enable autonomous switches
- Return or log access tokens, refresh tokens, client secrets, developer tokens

## Statuses

| Status | Meaning |
|--------|---------|
| `NOT_CONFIGURED` | Env credentials missing |
| `PARTIALLY_CONFIGURED` | Incomplete env pair/triplet |
| `NOT_CONNECTED` | OAuth integration missing |
| `DEMO` | Demo mode without live connection |
| `BLOCKED` | Missing confirmation / unsupported |
| `VERIFICATION_FAILED` | Live call failed (auth/authz/account/network) |
| `VERIFIED` | Read-only checks passed; **mutation still disabled** |

## APIs

| Method | Path | Permission |
|--------|------|------------|
| GET | `/api/v1/autopilot/operator/providers` | authenticated |
| POST | `/api/v1/autopilot/operator/providers/{meta\|google_ads}/preflight` | authenticated |
| POST | `/api/v1/autopilot/operator/providers/{provider}/verify` | `integration_connect` |
| GET | `/api/v1/autopilot/operator/providers/{provider}/verification` | authenticated |

Verify body:

```json
{ "confirm": "I_CONFIRM_READ_ONLY_PROVIDER_VERIFICATION", "client_id": null }
```

## Meta OAuth prerequisites (M6)

| Item | Detail |
|------|--------|
| Meta app type | **Business** app with Marketing API use cases (`Create & manage ads`, `Measure ad performance`). Consumer/login-only apps reject ads scopes. |
| Env | `META_APP_ID`, `META_APP_SECRET`, optional `META_REDIRECT_URI` (set in root `.env` **and** `apps/api/.env` when API may start from either cwd) |
| Redirect | `{API_PUBLIC_URL}/api/v1/integrations/meta/callback` must be allowlisted under **Facebook Login for Business** Valid OAuth Redirect URIs |
| Scopes | `ads_read`, `ads_management`, `business_management` (ad insights via `ads_read`; do **not** request `read_insights`) |
| Access | Development / Standard Access is enough for app-role users on ad accounts they administer; Advanced Access only for third-party accounts (App Review) |
| Storage | Encrypted Fernet blob (`ENCRYPTION_KEY`); never logged |
| Token lifecycle | Short-lived code exchange → **long-lived** `fb_exchange_token`; `ensure_meta_access_token` renews near expiry |
| Discovery | On connect: `/me` + `/me/adaccounts` → `config.meta_user_id`, `config.external_account_id` (`act_*`), `config.ad_accounts` |
| Status vocabulary | **CONFIGURED** = App ID/Secret present · **Connected** = OAuth tokens stored · **VERIFIED** = read-only provider verification passed · Verified ≠ autonomous spend |

Helpers: `apps/api/app/integrations/meta_oauth.py`

### Read-only verification (after Connect)

1. GrowthOS → **Integrations** → Meta → **Connect** → authorize in Meta.
2. Operator → **Refresh verification** (confirm phrase `I_CONFIRM_READ_ONLY_PROVIDER_VERIFICATION`).
3. Expect stage **VERIFIED**; still keep `AUTONOMOUS_EXECUTION_ENABLED=false`, `CANARY_ENABLED=false` until a separate controlled canary.

### Controlled canary (separate step — not part of OAuth)

Allowlists + confirm phrase only; never enable unrestricted autonomy after a successful canary.


## Google OAuth prerequisites (M7)

| Item | Detail |
|------|--------|
| Env | `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `GOOGLE_ADS_DEVELOPER_TOKEN` |
| Optional | `GOOGLE_ADS_LOGIN_CUSTOMER_ID` (MCC), `GOOGLE_ADS_REDIRECT_URI` |
| Scope | `https://www.googleapis.com/auth/adwords` + `openid` + `email` |
| Storage | Encrypted Fernet blob; refresh_token + access_token |
| Token lifecycle | `ensure_access_token` refreshes when stale |
| Discovery | `customers:listAccessibleCustomers` → `config.customers[]`, `config.customer_id` |
| Supported mutations | pause / resume campaign status only |
| Unsupported | Google campaign budget mutate (`campaignBudget`) |

Helpers: `apps/api/app/integrations/google_ads_discovery.py`

## Manual verification (when credentials exist)

1. Configure Meta or Google env vars (see `.env.example`) — reuse existing names.
2. Complete OAuth connect for the org/client in Integrations UI.
3. Open `/autopilot/operator` → **Verify provider**, or:

```bash
curl -X POST "$API/api/v1/autopilot/operator/providers/meta/verify" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"confirm":"I_CONFIRM_READ_ONLY_PROVIDER_VERIFICATION"}'
```

### Meta requirements

- `META_APP_ID`, `META_APP_SECRET`
- Connected Meta OAuth with Ads scopes
- Accessible ad account (`act_*`)

### Google Ads requirements

- `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `GOOGLE_ADS_DEVELOPER_TOKEN`
- Optional `GOOGLE_ADS_LOGIN_CUSTOMER_ID` (MCC)
- Connected Google Ads OAuth + accessible customer

If credentials are unavailable:

**REAL PROVIDER VERIFICATION NOT RUN — CREDENTIALS NOT CONFIGURED.**

## Related

- Live canary: [PRODUCTION_CANARY.md](./PRODUCTION_CANARY.md)
- Architecture: [ARCHITECTURE.md](./ARCHITECTURE.md)
