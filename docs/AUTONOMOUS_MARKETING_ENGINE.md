# Autonomous Marketing Engine

GrowthOS AI's autonomous marketing stack connects campaign generation, structured AI actions, autonomy policy, approval, platform execution, analytics feedback, and optimization — with explicit demo vs live separation and no fake external success.

## Architecture

```text
Client context → Strategy → Creative → Campaign build → AI Actions
    → Autonomy / approval → ExecutionEngine → Platform adapters
    → External IDs + audit → Analytics sync → Health scoring
    → Optimization rules / OptimizationAgent → new AI Actions
```

Long-running work (media, reports, campaign generation, publish-due) runs on the PostgreSQL-backed job queue, not in HTTP handlers.

## Execution lifecycle

| Stage | Component | Notes |
|-------|-----------|-------|
| Create | `ActionService.create` | Validates via `ActionValidator`, assigns idempotency key |
| Approve | `ActionService.approve` | Org-scoped, expiry checked, audit logged |
| Execute | `ExecutionEngine.execute` | Re-validates autonomy; tenant + target checks |
| Persist | `AIAction.result`, `ActionExecution` | External IDs stored when platform confirms |
| Audit | `write_audit` | No secrets in payloads |

### Status values

`PENDING`, `APPROVED`, `REJECTED`, `EXPIRED`, `EXECUTING`, `COMPLETED`, `FAILED`, `CANCELLED`, `SCHEDULED`

Execution modes: `DEMO_DATA`, `DEMO_EXECUTION`, `REAL_EXECUTION`

## Autonomy modes

| Mode | Behavior |
|------|----------|
| **copilot** | AI proposes; all external actions require approval |
| **assisted** | Low/medium may auto-run when automation enabled; high/critical require approval |
| **autonomous** | Category flags only (financial, publish, campaign create) |

Safety limits live on `AutonomySettings`: daily spend caps, campaign/creative/post rate limits, `max_ai_iterations`, `max_ai_actions_per_cycle`, `max_execution_time`, `max_failures_per_cycle`.

Blocked actions return structured errors (`AUTONOMY_LIMIT_EXCEEDED`, `BUDGET_LIMIT`, etc.) — never silent clamping.

## Provider capabilities

`GET /api/v1/autopilot/capabilities` returns per-provider operation status:

- `SUPPORTED` — adapter can execute when connected + external IDs present
- `UNSUPPORTED` — not implemented in this release
- `NOT_CONNECTED` — OAuth required
- `NOT_CONFIGURED` — env credentials missing

### Current write support

| Provider | Pause/resume | Budget update | Create campaign |
|----------|--------------|---------------|-------------------|
| Meta Ads | **Supported** (requires `campaign.external_id`) | **Supported** | Unsupported |
| Google Ads | **Supported** (requires synced resource id) | Unsupported | Unsupported |
| Instagram organic publish | Unsupported (scope) | — | — |

Live social publish adapters still return `PUBLISH_NOT_AVAILABLE` unless demo mode (simulated).

## Idempotency

- Actions: `idempotency_key` derived from org + type + target + payload
- Completed actions with the same key return the prior result on create
- Re-execute of `COMPLETED` actions is a no-op unless `force=True`
- Background jobs use `dedupe_key` (e.g. `exec:{action_id}` for reports)

## Tenant isolation

All action queries filter `organization_id`. Target validation ensures campaign/ad/client ownership before execution. Cross-tenant action access returns 404.

## Autopilot orchestration

- `POST /autopilot/run` — bounded build + proposal (stops at approval)
- `POST /autopilot/cycle` — bounded monitor/optimize cycle (`max_iterations` capped by autonomy settings)

Cycles persist summary in `AutopilotRun.result.cycles`.

## Optimization

- Deterministic health scoring with evidence strings
- Safe rule engine: structured `{metric, operator, value}` conditions (no arbitrary code)
- Rule matches spawn `AIAction` rows from `action_template`
- `OptimizationAgent` suggestions also create actions (capped per cycle)

## Production configuration

Required for live ads execution:

- Meta: `META_APP_ID`, `META_APP_SECRET`, OAuth with `ads_management`
- Google Ads: Google OAuth + `GOOGLE_ADS_DEVELOPER_TOKEN`
- Campaign must have platform-confirmed `external_id` (from sync or prior publish)

## Limitations (honest)

- Full campaign/ad creation on Meta/Google not enabled
- Instagram organic publishing requires additional OAuth scopes
- Autopilot does not auto-publish after approval without explicit action execution
- Optimization cycles are manual/API-triggered (no cron scheduler yet)

## Implementation map

| IMPLEMENTED | SUPPORTED (with config) | REQUIRES_CONFIGURATION | NOT_SUPPORTED |
|-------------|-------------------------|------------------------|---------------|
| Action pipeline, approval, audit | Meta pause/resume/budget | Live OAuth + external IDs | Full ad creation |
| Job queue, media generation | Google pause/resume | Developer token | Instagram organic publish |
| Analytics labeling | | | LinkedIn |
