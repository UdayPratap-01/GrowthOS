# Production Canary — Controlled Live Provider Execution (M5 Phase 2)

**Verdict:** GrowthOS is **code-ready for controlled canary testing** when credentials and allowlists are configured. It is **not** approved for unrestricted autonomous Meta/Google spend.

## Core distinctions

| Phrase | Means | Does NOT mean |
|--------|--------|----------------|
| Provider VERIFIED | Read-only connectivity + account/campaign discovery succeeded | Autonomous spend enabled |
| Canary successful | One explicit, allowlisted mutation was verified post-action | Unrestricted production autonomy approved |
| Autonomous OFF | Default safe posture | Canary cannot run (canary is separate, still gated) |

Progression (never skip):

```
READ-ONLY VERIFIED
  → CANARY CONFIGURED (allowlists non-empty)
  → DRY RUN PASSED
  → EXPLICIT HUMAN CONFIRMATION
  → SINGLE CONTROLLED CANARY ACTION (ActionService)
  → POST-ACTION VERIFICATION
  → RECONCILIATION IF UNCERTAIN
  → OPERATOR REVIEW
  → ONLY THEN consider next production stage
```

## Defaults (must remain OFF / empty)

| Gate | Default |
|------|---------|
| `CANARY_ENABLED` | `false` |
| `CANARY_ALLOWED_*` allowlists | empty = **deny all** |
| `AUTONOMOUS_EXECUTION_ENABLED` | `false` |
| `META_AUTONOMOUS_ENABLED` / `GOOGLE_AUTONOMOUS_ENABLED` | `false` |
| `OPTIMIZATION_ENABLED` | `false` |
| `AUTONOMOUS_KILL_SWITCH` | `false` |

Empty allowlists **never** mean allow-all.

## Architecture

Canary does **not** add a second execution engine or scheduler.

```
Operator API
  → canary gate (authoritative)
  → ActionService.create
  → ActionService.approve
  → ExecutionEngine
  → provider executor
  → AdsReconciler (post-action read)
  → UNKNOWN → existing reconciliation (no auto-retry)
```

Module: `apps/api/app/automation/canary.py`

## Confirmation phrases

| Use | Phrase |
|-----|--------|
| Read-only provider verify | `I_CONFIRM_READ_ONLY_PROVIDER_VERIFICATION` |
| Live canary execute | `I_CONFIRM_CANARY_LIVE_PROVIDER_EXECUTION` |

Do not reuse these interchangeably.

## Configuration

See `.env.example`. Key variables:

- `CANARY_ENABLED`
- `CANARY_ALLOWED_ORG_IDS`
- `CANARY_ALLOWED_PROVIDERS` (`meta`, `google_ads`)
- `CANARY_ALLOWED_META_AD_ACCOUNTS` / `CANARY_ALLOWED_META_CAMPAIGNS`
- `CANARY_ALLOWED_GOOGLE_CUSTOMERS` / `CANARY_ALLOWED_GOOGLE_CAMPAIGNS`
- `CANARY_ALLOWED_ACTIONS` (prefer `pause_campaign,resume_campaign`)
- `CANARY_ALLOWED_ENVIRONMENTS` (empty = none; e.g. `development,staging`)
- `CANARY_MAX_ACTIONS_PER_RUN` / `CANARY_MAX_ACTIONS_PER_DAY`
- `CANARY_MAX_SPEND_IMPACT` (pause/resume budget impact = 0)
- `PROVIDER_VERIFICATION_MAX_AGE_HOURS`

Also require org `automation_enabled=true` so ActionService can process financial actions.

## Gate codes

Examples: `BLOCKED_CANARY_DISABLED`, `BLOCKED_ORG_NOT_ALLOWLISTED`, `BLOCKED_ACCOUNT_NOT_ALLOWLISTED`, `BLOCKED_CAMPAIGN_NOT_ALLOWLISTED`, `BLOCKED_ACTION_NOT_ALLOWLISTED`, `BLOCKED_PROVIDER_NOT_VERIFIED`, `BLOCKED_STALE_VERIFICATION`, `BLOCKED_KILL_SWITCH`, `BLOCKED_CAPABILITY`, `BLOCKED_POLICY`, `BLOCKED_DAILY_LIMIT`, `BLOCKED_DUPLICATE`, `BLOCKED_RECONCILIATION`, `BLOCKED_INVALID_CONFIRM`.

## APIs

| Method | Path | Notes |
|--------|------|-------|
| GET | `/autopilot/operator/canary/status` | Readiness, limits, allowlist presence |
| GET | `/autopilot/operator/canary/history` | Canary AIAction history |
| POST | `/autopilot/operator/canary/dry-run` | Full gate path; **no mutation** |
| POST | `/autopilot/operator/canary/execute` | Live canary; confirm phrase + RBAC |

Members cannot dry-run or execute. Ownership of campaigns is resolved server-side.

## Supported actions (Phase 2)

Prefer:

- `pause_campaign`
- `resume_campaign`

Budget updates remain subject to existing capability / policy / risk limits. Google `update_budget` stays **UNSUPPORTED**.

Pause/resume: **budget impact = 0**, but pause remains **HIGH** business risk.

## Emergency stop

```bash
AUTONOMOUS_KILL_SWITCH=true
```

Blocks **new** live canary mutations. Does not delete recommendations or rewrite completed provider state. UI shows: **KILL SWITCH ACTIVE — NEW LIVE MUTATIONS BLOCKED**.

## Safe enablement sequence

1. Connect test Meta/Google integrations (never commit secrets).
2. Run read-only verify (`I_CONFIRM_READ_ONLY_PROVIDER_VERIFICATION`).
3. Note ad account / campaign IDs from `canary_resources`.
4. Set allowlists + `CANARY_ALLOWED_ENVIRONMENTS` for this environment.
5. Set org `automation_enabled=true`.
6. `CANARY_ENABLED=true`.
7. Operator UI → Dry Run until ALLOWED.
8. Execute once with `I_CONFIRM_CANARY_LIVE_PROVIDER_EXECUTION`.
9. Confirm post-verification; if UNKNOWN, use existing reconciliation — **do not auto-retry**.
10. Leave autonomous latches OFF unless a later milestone explicitly authorizes them.

## Real Meta canary checklist (M6)

Use a **dedicated low-risk Meta ad account** and a **single test campaign**. Stop on any failure.

```text
1. Meta App: META_APP_ID / META_APP_SECRET + redirect URI allowlisted
2. Integrations UI → Connect Meta (OAuth) → long-lived token stored encrypted
3. Confirm config.external_account_id is act_* (not Graph user id)
4. Read-only verify → VERIFIED + canary_resources campaigns listed
5. Create/sync GrowthOS Campaign row with external_id = Meta campaign id
6. Allowlists:
   CANARY_ENABLED=true
   CANARY_ALLOWED_ORG_IDS=<org>
   CANARY_ALLOWED_PROVIDERS=meta
   CANARY_ALLOWED_META_AD_ACCOUNTS=act_...
   CANARY_ALLOWED_META_CAMPAIGNS=<campaign_id>
   CANARY_ALLOWED_ACTIONS=pause_campaign,resume_campaign
   CANARY_ALLOWED_ENVIRONMENTS=<this env>
7. AUTONOMOUS_KILL_SWITCH=false (and keep AUTONOMOUS_EXECUTION_ENABLED=false)
8. Dry-run pause → ALLOWED
9. Execute pause → post-verify PAUSED
10. Dry-run resume → execute → post-verify ACTIVE
11. Optional: controlled budget update (tiny delta) → reconcile daily_budget
12. If UNKNOWN/timeout → reconcile only; never blind retry
```

Confirm phrases:

- Read-only: `I_CONFIRM_READ_ONLY_PROVIDER_VERIFICATION`
- Live canary: `I_CONFIRM_CANARY_LIVE_PROVIDER_EXECUTION`

**Canary success ≠ unrestricted production autonomy.**

## Real Google canary checklist (M7)

Use a **dedicated low-risk Google Ads customer** and a **single test campaign**. Stop on any failure.

```text
1. Google Cloud OAuth client + Google Ads API enabled
2. GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET / GOOGLE_ADS_DEVELOPER_TOKEN
3. Optional GOOGLE_ADS_LOGIN_CUSTOMER_ID for MCC
4. Integrations UI → Connect Google Ads → customers[] discovered
5. Read-only verify → VERIFIED + canary_resources campaigns
6. Sync/create GrowthOS Campaign with external_id + metrics.customer_id
7. Allowlists:
   CANARY_ENABLED=true
   CANARY_ALLOWED_ORG_IDS=<org>
   CANARY_ALLOWED_PROVIDERS=google_ads
   CANARY_ALLOWED_GOOGLE_CUSTOMERS=<customer_id>
   CANARY_ALLOWED_GOOGLE_CAMPAIGNS=<campaign_id>
   CANARY_ALLOWED_ACTIONS=pause_campaign,resume_campaign
   CANARY_ALLOWED_ENVIRONMENTS=<this env>
8. Keep AUTONOMOUS_EXECUTION_ENABLED=false; AUTONOMOUS_KILL_SWITCH=false for canary
9. Dry-run pause → ALLOWED
10. Execute pause → post-verify PAUSED (Google status PAUSED)
11. Dry-run resume → execute → post-verify ENABLED/ACTIVE
12. Budget update: NOT supported in M7 (campaignBudget resource) — skip
13. If UNKNOWN/timeout → reconcile only; never blind retry
```

**MOCKED GOOGLE VERIFICATION** = CI tests with HTTP mocks.  
**REAL GOOGLE VERIFICATION** = this checklist with live credentials.  
**PRODUCTION SIGN-OFF** = later autonomous spend approval (not M7).

**Canary success ≠ unrestricted production autonomy.**

## Rollback

1. Set `AUTONOMOUS_KILL_SWITCH=true` and/or `CANARY_ENABLED=false`.
2. Clear allowlists if needed.
3. Resolve UNKNOWN actions via operator reconciliation UI.
4. Manually reverse campaign state in Ads Manager if required.

## Troubleshooting

| Symptom | Check |
|---------|--------|
| NOT_CONFIGURED | Empty allowlists or environments |
| BLOCKED_STALE_VERIFICATION | Re-run read-only verify |
| BLOCKED_KILL_SWITCH | Kill switch on |
| BLOCKED_RECONCILIATION | Resolve UNKNOWN first |
| Dry-run OK, execute blocked | Confirm phrase / kill switch flipped between calls |
| Account allowlist miss | Prefer `act_*` from OAuth discovery / verify snapshot |

## Related docs

- [PROVIDER_VERIFICATION.md](./PROVIDER_VERIFICATION.md) — Phase 1 read-only + Meta OAuth
- [AUTONOMOUS_MARKETING_ENGINE.md](./AUTONOMOUS_MARKETING_ENGINE.md)
- [ARCHITECTURE.md](./ARCHITECTURE.md)
