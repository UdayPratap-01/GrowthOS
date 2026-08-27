# Production Canary Procedure — Autonomous Marketing

**Verdict:** GrowthOS is **not** production-ready for live autonomous Meta/Google spend until live provider verification is completed with real credentials.

This document is the controlled enablement sequence for a single canary organization.

## Defaults (must remain)

| Gate | Default |
|------|---------|
| `AUTONOMOUS_EXECUTION_ENABLED` | `false` |
| `META_AUTONOMOUS_ENABLED` | `false` |
| `GOOGLE_AUTONOMOUS_ENABLED` | `false` |
| `OPTIMIZATION_ENABLED` | `false` |
| `AUTONOMOUS_KILL_SWITCH` | `false` |
| `AUTOPILOT_SCHEDULER_ENABLED` | `false` |
| `PROVIDER_VERIFICATION_ENABLED` | `false` |
| Canary org/provider/action lists | empty (none allowed) |

Empty canary lists mean **no** autonomous mutations.

## Emergency stop

```bash
AUTONOMOUS_KILL_SWITCH=true
```

Effect:

- Analysis / recommendations continue
- Dashboards continue
- **NEW autonomous mutation AIActions are blocked** (`AUTONOMOUS_KILL_SWITCH_ENABLED`)
- Operator-approved recommendation approval still follows approval policy (re-evaluates policy)
- In-flight actions are not silently corrupted
- Recommendations are not deleted

## Canary enablement sequence

1. Connect a **test** Meta and/or Google Ads account (sandbox / non-production spend).
2. Confirm credentials via integrations UI — never paste tokens into tickets.
3. Choose **one** organization UUID, **one** client, **one** campaign with a known `external_id`.
4. Set canary allowlists:

```bash
AUTONOMOUS_CANARY_ORG_IDS=<org-uuid>
AUTONOMOUS_CANARY_PROVIDERS=meta
AUTONOMOUS_CANARY_ACTION_TYPES=update_budget
AUTONOMOUS_MAX_CAMPAIGNS_PER_CYCLE=1
AUTONOMOUS_MAX_DAILY_SPEND_IMPACT=50
```

5. Enable provider + global autonomous latch **only after** allowlists:

```bash
META_AUTONOMOUS_ENABLED=true
AUTONOMOUS_EXECUTION_ENABLED=true
```

6. Enable optimization analysis + closed loop:

```bash
OPTIMIZATION_ENABLED=true
```

7. In Autopilot settings for that org/client:

- `automation_enabled=true`
- Start with `assisted` (APPROVAL_REQUIRED) — **not** autonomous
- Strict `maximum_budget_increase_percentage` / `maximum_campaign_budget`
- Narrow `allowed_actions`

8. Ingest analytics → run performance intelligence → review recommendation evidence.
9. Manually **approve** the first recommendation from Operator UI (`/autopilot/recommendations`).
10. Observe provider state in Ads Manager; confirm `AIAction` COMPLETED (or reconcile if ambiguous).
11. Verify analytics ingestion reflects the change.
12. Only then consider `autonomy_mode=autonomous` with `require_approval_for_financial_actions=false` for **LOW** risk only (`OPTIMIZATION_MAX_AUTONOMOUS_RISK=low`).

## Live provider verification

```bash
PROVIDER_VERIFICATION_ENABLED=true
PROVIDER_VERIFICATION_CONFIRM=I_CONFIRM_LIVE_MUTATIONS
PROVIDER_VERIFICATION_ORG_ID=...
PROVIDER_VERIFICATION_CLIENT_ID=...
PROVIDER_VERIFICATION_META_CAMPAIGN_ID=...   # test campaign only
PROVIDER_VERIFICATION_GOOGLE_CAMPAIGN_ID=... # optional
```

If credentials are unavailable:

**LIVE PROVIDER VERIFICATION NOT RUN — CREDENTIALS REQUIRED**

That is an acceptable outcome. Do not claim live verification.

## Operator surfaces

- `/autopilot/operator` — status / kill switch / providers
- `/autopilot/recommendations` — approve/reject (backend re-eval)
- `/autopilot/actions` + detail — lifecycle
- `/autopilot/reconciliation` — UNKNOWN resolve + legacy EXECUTING
- `/autopilot/settings` — read-only safety snapshot

## Rollback

1. `AUTONOMOUS_KILL_SWITCH=true`
2. `AUTONOMOUS_EXECUTION_ENABLED=false`
3. `OPTIMIZATION_ENABLED=false` (optional; stops closed-loop creates)
4. Clear canary lists
5. Resolve any UNKNOWN actions manually — never auto-re-execute
