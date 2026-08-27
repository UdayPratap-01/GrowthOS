# Provider Verification — Phase 1 (Read-Only)

**PROVIDER VERIFIED does not mean AUTONOMOUS SPEND ENABLED.**

Phase 1 proves GrowthOS can establish the intended **read-only** connection to Meta / Google Ads. It does **not** authorize mutations, closed-loop execution, or live spend.

## What Phase 1 does

1. Local **preflight** (no network): app credentials present? OAuth integration connected?
2. Explicit operator confirmation
3. Read-only API checks (when connected):
   - Meta: `/me`, `/me/adaccounts`, optional campaigns list
   - Google Ads: `customers:listAccessibleCustomers`, optional campaign search
4. Structured result + audit + metrics
5. Persist sanitized snapshot on `integrations.config.last_verification` (no secrets)

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

## Manual verification (when credentials exist)

1. Configure Meta or Google env vars (see `.env.example`) — reuse existing names.
2. Complete OAuth connect for the org/client in Integrations UI.
3. Open `/autopilot/operator` → **Verify provider**, or:

```bash
# API (example)
curl -X POST "$API/api/v1/autopilot/operator/providers/meta/verify" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"confirm":"I_CONFIRM_READ_ONLY_PROVIDER_VERIFICATION"}'
```

### Meta requirements

- `META_APP_ID`, `META_APP_SECRET`
- Connected Meta OAuth (`ads_read` / business scopes as configured)
- Accessible ad account

### Google Ads requirements

- `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `GOOGLE_ADS_DEVELOPER_TOKEN`
- Optional `GOOGLE_ADS_LOGIN_CUSTOMER_ID` (MCC)
- Connected Google Ads OAuth + accessible customer

If credentials are unavailable:

**REAL PROVIDER VERIFICATION NOT RUN — CREDENTIALS NOT CONFIGURED.**

## Safety defaults (unchanged)

`AUTONOMOUS_EXECUTION_ENABLED`, `META_AUTONOMOUS_ENABLED`, `GOOGLE_AUTONOMOUS_ENABLED`,
`OPTIMIZATION_ENABLED`, `AUTONOMOUS_KILL_SWITCH` remain **false** by default.

See also: [PRODUCTION_CANARY.md](./PRODUCTION_CANARY.md), [AUTONOMOUS_MARKETING_ENGINE.md](./AUTONOMOUS_MARKETING_ENGINE.md).
