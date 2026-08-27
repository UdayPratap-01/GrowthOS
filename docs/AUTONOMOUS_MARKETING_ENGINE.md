# Autonomous Marketing Engine

GrowthOS AI's autonomous marketing stack connects campaign generation, structured AI actions, autonomy policy, approval, platform execution, analytics feedback, and optimization — with explicit demo vs live separation and no fake external success.

## Architecture

```text
Client context → Strategy → Creative → Campaign build → AI Actions
    → Autonomy / approval → ExecutionEngine → Platform adapters
    → External IDs + audit → Analytics sync → Health scoring
    → Optimization rules / OptimizationAgent → new AI Actions
```

### Closed-loop optimization (Milestone 3)

```text
MarketingPerformanceDaily
  → Performance Intelligence → PerformanceRecommendation
  → Optimization Decision Engine (map only; never executes)
  → Policy Engine (deterministic BLOCKED | allow)
  → MANUAL | APPROVAL_REQUIRED | AUTONOMOUS
  → AIAction via ActionService (existing pipeline only)
  → ExecutionEngine → Meta / Google
  → Analytics ingestion → Intelligence → next decision
```

Default latch: `OPTIMIZATION_ENABLED=false`. Existing orgs do **not** become autonomous from this milestone alone.

Long-running work (media, reports, campaign generation, publish-due) runs on the PostgreSQL-backed job queue, not in HTTP handlers.

## Execution lifecycle

| Stage | Component | Notes |
|-------|-----------|-------|
| Create | `ActionService.create` | Validates via `ActionValidator`, assigns idempotency key |
| Approve | `ActionService.approve` | Org-scoped, expiry checked, audit logged |
| Execute | `ExecutionEngine.execute` | Re-validates autonomy; tenant + target checks; sets `executing_at` on claim |
| Recover | Worker `reap_stale_executing_actions` | CAS stale `EXECUTING` → `FAILED`; audit logged |
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
- **Scheduled autopilot** — optional worker-driven cycles (see below)

Cycles persist summary in `AutopilotRun.result.cycles`.

## Scheduled autopilot execution

When enabled, the existing background worker schedules bounded autopilot cycles through the same job queue used for media and reports. The scheduler **never** calls platform APIs directly — it only enqueues `autopilot.cycle` jobs that invoke `AutopilotOrchestratorService` (identical orchestration path to `POST /autopilot/cycle`).

### Architecture

```text
Worker cycle
  → ensure_scheduler_tick (if AUTOPILOT_SCHEDULER_ENABLED)
  → autopilot.scheduler_tick job (dedupe per time window)
       → discover orgs with automation_enabled
       → enqueue autopilot.cycle per eligible client (dedupe per org/client/window)
       → schedule next tick
  → worker claims autopilot.cycle jobs
       → AutopilotOrchestratorService.run_cycle
            → OptimizationService (creates AIAction rows only)
            → ClosedLoopOptimizer.process_client_recommendations
                 (only when OPTIMIZATION_ENABLED=true)
```

### Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `AUTOPILOT_SCHEDULER_ENABLED` | `false` | Master switch — disabled by default |
| `AUTOPILOT_INTERVAL_MINUTES` | `60` | Minimum 5; scheduler window size |
| `AUTOPILOT_MAX_ORGS_PER_CYCLE` | `10` | Max distinct organizations enqueued per tick |

### Idempotency

- Scheduler tick: `autopilot-scheduler:{window_start_iso}` (unique `dedupe_key`)
- Cycle job: `autopilot:{organization_id}:{client_id}:{window_start_iso}`
- Concurrent workers racing on the same window get the same job row back

### Overlapping cycle protection

Before enqueueing a new cycle for an organization, the scheduler checks for an in-flight `autopilot.cycle` job (`queued`, `retrying`, or `running`). If one exists, the org is skipped for that tick.

### Failure isolation

Per-organization enqueue failures during a tick are logged and counted; other organizations continue processing. Handler failures retry via the standard job queue backoff.

### Safety controls preserved

- `automation_enabled` required on effective autonomy settings
- Demo organizations flow through `AutopilotOrchestratorService` with `organization.demo_mode` intact
- Action creation still passes `ActionValidator`; execution still requires approval/autonomy policy
- No scheduler shortcut bypasses `ExecutionEngine`

### Local enablement

```bash
# In apps/api/.env or docker-compose worker environment:
AUTOPILOT_SCHEDULER_ENABLED=true
AUTOPILOT_INTERVAL_MINUTES=60
AUTOPILOT_MAX_ORGS_PER_CYCLE=10

# Run the worker (scheduler lives here, not in the API process):
python -m app.worker
```

Production: run at least one dedicated worker process with the scheduler enabled. Multiple workers are safe — dedupe keys and job claiming prevent duplicate ticks/cycles.

## Stale execution recovery

When a worker crashes mid-action, `AIAction.status` can remain `EXECUTING`. The worker process runs a bounded reaper each cycle (same path as job lease recovery):

```text
Worker.run_once()
  → reap_stale_executing_actions()
       → find EXECUTING where executing_at <= now - timeout
       → atomic CAS → FAILED (retryable)
       → audit: ai_action.stale_recovery
```

| Variable | Default | Description |
|----------|---------|-------------|
| `AUTONOMOUS_EXECUTION_STALE_TIMEOUT_MINUTES` | `30` | Minimum 5 — staleness threshold |
| `AUTONOMOUS_EXECUTION_STALE_RECOVERY_BATCH_SIZE` | `50` | Max actions recovered per worker cycle |

Behavior:
- `executing_at` is set atomically when an action is claimed for execution
- Recovery moves stale actions to `FAILED` with `STALE_EXECUTION_RECOVERED` error
- Does **not** auto-re-execute — normal claim/validation path applies on retry
- Multiple workers safe via conditional UPDATE (same pattern as job claiming)
- Open `ActionExecution` rows for the action are marked failed

## Provider timeout reconciliation

When an ads mutation HTTP request times out or fails at the transport layer, the outcome is **ambiguous** — the platform may have applied the change.

### Timeout classification

| Class | Error codes | Behavior |
|-------|-------------|----------|
| **Ambiguous** | `PROVIDER_TIMEOUT_AMBIGUOUS`, `PROVIDER_TRANSPORT_AMBIGUOUS` | Action → `FAILED` with `reconciliation.state=PENDING`; **no auto re-execute** |
| **Confirmed failure** | `HTTP_*`, `INTEGRATION_NOT_CONNECTED`, `UNSUPPORTED_OPERATION`, etc. | Normal retryable `FAILED` |
| **Confirmed success** | Provider body confirms mutation | `COMPLETED` |

### Reconciliation (read-only)

Job type: `provider.reconcile` (dedupe: `provider-reconcile:{action_id}`)

Outcomes:
- `CONFIRMED_SUCCESS` → action `COMPLETED`
- `CONFIRMED_NOT_APPLIED` → `FAILED` (retryable; reconciliation cleared)
- `UNKNOWN` → `FAILED` (blocked from auto re-execute)
- `UNSUPPORTED` → `UNKNOWN` (honest; no fake lookup)

### Supported reconciliation lookups

| Provider | Operations |
|----------|------------|
| Meta | pause, resume, update_budget (GET campaign status/budget) |
| Google Ads | pause, resume (GAQL status search) |
| Google Ads | update_budget — **unsupported** |
| Either | create_campaign/ad/ad_set — **unsupported** |

Audit: `ai_action.ambiguous` (on timeout), `ai_action.provider_reconciled` (on reconciliation)

## Optimization (legacy rules + agent)

- Deterministic health scoring with evidence strings
- Safe rule engine: structured `{metric, operator, value}` conditions (no arbitrary code)
- Rule matches spawn `AIAction` rows from `action_template`
- `OptimizationAgent` suggestions also create actions (capped per cycle)

These paths remain separate from Milestone 3 closed-loop optimization (performance recommendations).

## Closed-loop decision & policy engines

Package: `app/optimization/` (`decision`, `policy`, `modes`, `risk`, `closed_loop`).

### Decision engine

Consumes a `PerformanceRecommendation` and returns a structured `OptimizationDecision`. **Never** calls Meta/Google or `ExecutionEngine`.

| Outcome | Meaning |
|---------|---------|
| `ACTION` | Policy passed; AIAction created (autonomous path) |
| `APPROVAL_REQUIRED` | Policy passed; awaiting human approve before AIAction |
| `NO_ACTION` | Unsupported / informational / MANUAL mode |
| `BLOCKED` | Policy or capability failure with auditable checks |

Supported executable mappings (evidence-bound; no invented metrics):

| Recommendation `suggested_action.operation` | AIActionType | Notes |
|---------------------------------------------|--------------|-------|
| `UPDATE_BUDGET` | `update_budget` | Requires known current daily budget |
| `PAUSE_CAMPAIGN` | `pause_campaign` | HIGH risk → approval by default |
| `RESUME_CAMPAIGN` | `resume_campaign` | MEDIUM risk |
| Other / informational | — | `NO_ACTION` |

Decisions are stored on the recommendation (`suggested_action.last_decision`) for audit/API listing. No new decision table.

### Policy engine

Deterministic checks; **never silently clamps** budget changes (e.g. 50% request with 20% max → `BLOCKED`).

| Rule | Source |
|------|--------|
| Min spend / impressions / clicks / conversions | `PERFORMANCE_MIN_*` |
| Min confidence | `OPTIMIZATION_MIN_CONFIDENCE` |
| Recommendation not expired / rejected | recommendation lifecycle |
| Action + platform allowlists | `AutonomySettings` |
| Provider capability + connected + credentials | capability registry |
| External campaign ID present | campaign / recommendation |
| Max budget increase / decrease % | `AutonomySettings` (no clamp) |
| Min / max campaign budget | `OPTIMIZATION_MIN_CAMPAIGN_BUDGET` + settings |
| Max closed-loop actions / 24h | `OPTIMIZATION_MAX_ACTIONS_PER_DAY` |
| Same-action cooldown | `OPTIMIZATION_COOLDOWN_HOURS` |
| Opposite-action cooldown (pause↔resume) | `OPTIMIZATION_OPPOSITE_COOLDOWN_HOURS` |
| Max consecutive budget increases | `OPTIMIZATION_MAX_CONSECUTIVE_BUDGET_INCREASES` |
| Duplicate action for same recommendation | existing `AIAction` rows |
| Ambiguous prior action (reconciliation PENDING/UNKNOWN) | no new duplicate |
| HIGH risk vs max autonomous risk | `OPTIMIZATION_MAX_AUTONOMOUS_RISK` (HIGH never auto solely on confidence) |

### Product autonomy modes (mapped from existing settings)

| Mode | When | Behavior |
|------|------|----------|
| **MANUAL** | `automation_enabled=false` or `copilot` | Evaluate only; never create AIAction |
| **APPROVAL_REQUIRED** | `assisted`, or `autonomous` with financial approval | Create path only after `POST .../recommendations/{id}/approve` + **policy re-eval** |
| **AUTONOMOUS** | `autonomous` + automation on + financial approval off | Create AIAction after policy/capability/idempotency |

HIGH-risk actions (pause, large budget change) remain approval-gated even in AUTONOMOUS when above `OPTIMIZATION_MAX_AUTONOMOUS_RISK` (default `low`).

### Risk classification

| Risk | Examples | Autonomy |
|------|----------|----------|
| LOW | Budget change ≤ 10% within policy | May auto if mode + max risk allow |
| MEDIUM | Budget 10–20%; resume | Depends on `OPTIMIZATION_MAX_AUTONOMOUS_RISK` |
| HIGH | Pause; budget > 20% | Never autonomous from confidence alone |

### Cooldown & loop protection

Uses existing `AIAction` history (`agent=closed_loop_optimizer`) — no duplicate state store.

- Same campaign + same action type within cooldown → blocked
- Pause after recent resume (and reverse) within opposite cooldown → blocked
- Repeated budget increases capped without new evidence window

### Approval flow

`POST /api/v1/analytics/recommendations/{id}/approve` → policy **re-evaluated** at approval time. Rejected if expired, cooldown, budget/policy change, disconnected provider, missing external ID, or evidence invalid.

`POST .../reject` marks recommendation rejected (no AIAction).

### Autopilot integration

When `OPTIMIZATION_ENABLED=true`, each `AutopilotOrchestratorService.run_cycle` calls `ClosedLoopOptimizer.process_client_recommendations` after intelligence. Uses the **same** scheduler/JobQueue — no second scheduler. Cycle is idempotent: duplicate recommendation actions are blocked by policy + ActionService idempotency.

### APIs (minimum)

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/autopilot/optimization/policies` | Effective thresholds + mode explanation |
| GET | `/autopilot/optimization/status` | Latch, mode, recent closed-loop counts |
| GET | `/autopilot/optimization/decisions` | Recent `last_decision` snapshots |
| POST | `/analytics/recommendations/{id}/approve` | Re-eval + optional AIAction |
| POST | `/analytics/recommendations/{id}/reject` | Reject recommendation |

Tenant isolation + RBAC on all routes. Prefer extending these over duplicating legacy `/optimization/rules` endpoints.

### Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `OPTIMIZATION_ENABLED` | `false` | Global closed-loop latch |
| `OPTIMIZATION_MIN_CONFIDENCE` | `0.55` | Below → BLOCKED |
| `OPTIMIZATION_COOLDOWN_HOURS` | `24` | Same action cooldown |
| `OPTIMIZATION_OPPOSITE_COOLDOWN_HOURS` | `48` | Opposite action cooldown |
| `OPTIMIZATION_MAX_ACTIONS_PER_DAY` | `10` | Org closed-loop create cap |
| `OPTIMIZATION_MAX_CONSECUTIVE_BUDGET_INCREASES` | `2` | Loop protection |
| `OPTIMIZATION_MIN_CAMPAIGN_BUDGET` | `5` | Floor (currency units) |
| `OPTIMIZATION_MAX_AUTONOMOUS_RISK` | `low` | Cap for auto-create |

### Audit events

`optimization.recommendation_evaluated`, `optimization.policy_blocked`, `optimization.approval_required`, `optimization.action_created`, `optimization.action_skipped`, `optimization.cooldown_blocked`, `optimization.autonomous_action_created` — payloads sanitized (no tokens/secrets).

Provider Phase 1 (see [PROVIDER_VERIFICATION.md](./PROVIDER_VERIFICATION.md)): `provider.preflight_*`, `provider.verification_*`.

### Live canary Phase 2 (Milestone 5)

See [PRODUCTION_CANARY.md](./PRODUCTION_CANARY.md).

- Module: `app/automation/canary.py` — single gate; empty allowlists = deny
- APIs: `GET/POST /autopilot/operator/canary/{status,history,dry-run,execute}`
- Confirm: `I_CONFIRM_CANARY_LIVE_PROVIDER_EXECUTION` (not the read-only phrase)
- Preferred actions: `pause_campaign`, `resume_campaign` via ActionService only
- Post-action: AdsReconciler; UNKNOWN → existing reconciliation (no auto-retry)
- Audit: `canary.dry_run`, `canary.blocked`, `canary.execution_*`, `canary.post_verification_*`, `canary.reconciliation_required`
- **Verified ≠ autonomous spend. Canary success ≠ unrestricted autonomy.**

### Database

No new Milestone 3 migration. Reuses `performance_recommendations`, `ai_actions`, `action_executions`, `autonomy_settings`, and audit events. Decision snapshots live in recommendation JSON.

## Production safety & operator control (Milestone 4)

See [PRODUCTION_CANARY.md](./PRODUCTION_CANARY.md).

Layered gates (all default off / empty):

- `AUTONOMOUS_EXECUTION_ENABLED`
- `META_AUTONOMOUS_ENABLED` / `GOOGLE_AUTONOMOUS_ENABLED`
- `OPTIMIZATION_ENABLED`
- `AUTONOMOUS_KILL_SWITCH` (blocks NEW autonomous mutations only)
- Canary org / provider / action allowlists (empty = none)
- Org/client `AutonomySettings` + action allowlist
- Existing policy / risk / capability checks

Operator APIs: `/autopilot/operator/status`, ambiguous actions, action detail, manual UNKNOWN resolve, legacy EXECUTING recovery.

## Production configuration

Required for live ads execution:

- Meta: `META_APP_ID`, `META_APP_SECRET`, OAuth with `ads_management`
- Google Ads: Google OAuth + `GOOGLE_ADS_DEVELOPER_TOKEN`
- Campaign must have platform-confirmed `external_id` (from sync or prior publish)

## Limitations (honest)

- Full campaign/ad creation on Meta/Google not enabled
- Instagram organic publishing requires additional OAuth scopes
- Autopilot does not auto-publish after approval without explicit action execution
- Scheduled autopilot creates actions only; it does not auto-execute approved actions unless autonomy policy already allows that through the normal pipeline
- Closed-loop optimization is **not** live-production-ready: Meta/Google credentials and spend verification remain separate
- Google Ads budget update remains unsupported (capability → BLOCKED)
- `OPTIMIZATION_ENABLED` defaults false; enabling still requires correct autonomy settings and connected integrations
- Demo mode must stay safe — closed loop still goes through ActionService / ExecutionEngine demo paths

## Implementation map

| IMPLEMENTED | SUPPORTED (with config) | REQUIRES_CONFIGURATION | NOT_SUPPORTED |
|-------------|-------------------------|------------------------|---------------|
| Action pipeline, approval, audit | Meta pause/resume/budget | Live OAuth + external IDs | Full ad creation |
| Job queue, media generation | Google pause/resume | Developer token | Instagram organic publish |
| Analytics + performance intelligence | Closed-loop decision/policy | `OPTIMIZATION_ENABLED` + autonomy | Google budget update |
| Closed-loop → AIAction (gated) | | Connected provider | Unrestricted auto budget |
| Analytics labeling | | | LinkedIn |
