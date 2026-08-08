# GrowthOS AI — Autonomous Marketing Architecture

## Current architecture (Phases 1–5)

Monorepo:

- `apps/web` — Next.js App Router, design system, client workspaces
- `apps/api` — FastAPI, SQLAlchemy, JWT auth, multi-tenant services
- Postgres/SQLite via `create_all` (+ Alembic scaffold)

### Already working

| Layer | Components |
|-------|------------|
| Auth / tenancy | JWT, org membership, `organization_id` scoping |
| Clients / CRM | Clients, leads, kanban, deterministic lead scoring |
| AI core | `AIProvider` (mock/openai/anthropic), `AIOrchestrator`, Strategy/Content/Lead/Analytics/Ads/Report/Creative/Optimization/Monitoring agents |
| Content Studio | Generate/save/calendar (text) |
| Analytics / reports | DB-backed periods, PDF reports |
| Integrations | Meta/IG/WA/GA/Ads/YouTube OAuth + sync; honest statuses |
| Phase 5 Autopilot | Autonomy settings, `AIAction`, Approval/Execution engines, BudgetGuard, decision loop, Autopilot/Approvals/AI Activity UI |
| Generation | Image/Video provider ABCs (`none` / `demo`) |
| Publishing | SocialPublisher adapters (honest not-connected / DEMO) |
| Jobs | DB-backed `JobQueue` |
| Mode | DEMO vs LIVE effective mode + KPI `demo`/`live`/`mixed` |

### Partially working

- Live Meta/Google **write** (create campaign, publish) — adapters return honest “not available” without write scopes
- Background jobs — queue exists; no dedicated Redis/Celery worker process
- Webhooks — Meta signature validation only

### Demo-only

- Seed analytics/campaigns
- Ads/publish simulation when DEMO mode
- Image/video “demo” providers (concepts only, labeled DEMO DATA)

### Missing for Autonomous Marketing Engine

1. One-click **Run Marketing Autopilot** with live step progress
2. **AI Campaign Builder** UI + multi-step build workflow
3. **CampaignPlannerAgent**, **AutopilotAgent**, named Image/Video creative agents
4. **Creative Library** UI
5. Autonomy fields: `maximum_actions_per_day`, cycle limits, client overrides capped by org
6. `GENERATE_CREATIVE_VARIATIONS` action type
7. Campaign workspace AI optimize entrypoint

---

## Proposed extensions (reuse first)

```
ClientContext (existing schema)
      ↓
AIOrchestrator (+ CampaignPlanner / Autopilot coordination)
      ↓
Structured AIAction (existing ActionService)
      ↓
ApprovalEngine / Autonomy (existing safety + modes)
      ↓
ExecutionEngine (existing)
      ↓
Integrations / Image / Video / SocialPublisher
      ↓
MonitoringAgent + OptimizationAgent (existing)
```

Do **not** replace Action/Execution engines. Add:

- `AutopilotRun` model for multi-step progress
- `CampaignBuildService` / `AutopilotRunService`
- Frontend: `/campaign-builder`, `/creative-library`, Autopilot “Start” panel

---

## Database changes

| Change | Notes |
|--------|-------|
| `autonomy_settings` new columns | `maximum_actions_per_day`, `max_ai_iterations`, `max_ai_actions_per_cycle`, `max_execution_time`, `max_failures_per_cycle` |
| `ai_action_type` | add `GENERATE_CREATIVE_VARIATIONS` |
| `autopilot_runs` | new table: client, goal, budget, steps JSON, status |
| `creative_assets` | already exists — reuse for library |

SQLite/local continues via `create_all`.

---

## API changes

Extend `/api/v1/autopilot`:

- `POST /autopilot/run` — start one-click autopilot
- `GET /autopilot/runs/{id}` — progress
- `POST /autopilot/campaigns/build` — AI Campaign Builder
- `GET /autopilot/creative/library` — creative library
- `POST /autopilot/creative/variations` — variations

Reuse existing approve/reject/execute/decision-loop/settings.

---

## AI architecture

New agents (modular, typed I/O):

- `CampaignPlannerAgent` — campaign structure proposal
- `AutopilotAgent` — high-level run plan / step list
- `ImageCreativeAgent` — image prompts/concepts (calls ImageGenerationProvider via services)
- `VideoAgent` — video concepts/scripts (calls VideoGenerationProvider via services)
- `CompetitorAgent` — qualitative competitor notes from stored competitors

Agents never call platform APIs; ExecutionEngine + adapters do.

---

## Execution / security

Unchanged honesty rules:

- No fake campaign/post/video IDs
- DEMO DATA labeled
- LIVE never silent-falls back to demo success
- Budget + rate limits on create and execute
- Tenant + client ownership checks

---

## Implementation phases

1. Architecture doc ✅  
2. Schema/settings + agents  
3. Build/Run services + APIs  
4. Frontend surfaces  
5. Tests + implementation report  

---

## Testing strategy

- Unit/API: build creates PENDING actions; budget blocks; image NOT CONFIGURED  
- Autopilot run progresses steps without claiming live publish  
- Tenant isolation regression  
- Frontend tsc  
