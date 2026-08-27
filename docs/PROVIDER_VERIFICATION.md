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
| Env | `META_APP_ID`, `META_APP_SECRET`, optional `META_REDIRECT_URI` |
| Redirect | `{API_PUBLIC_URL}/api/v1/integrations/meta/callback` must be allowlisted in Meta App |
| Scopes | `ads_read`, `ads_management`, `business_management`, `read_insights` |
| Storage | Encrypted Fernet blob (`ENCRYPTION_KEY`); never logged |
| Token lifecycle | Short-lived code exchange → **long-lived** `fb_exchange_token`; `ensure_meta_access_token` renews near expiry |
| Discovery | On connect: `/me` + `/me/adaccounts` → `config.meta_user_id`, `config.external_account_id` (`act_*`), `config.ad_accounts` |

Helpers: `apps/api/app/integrations/meta_oauth.py`

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
