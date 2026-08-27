# GrowthOS AI — System Architecture

## Product Overview

GrowthOS AI is a multi-tenant SaaS digital marketing operating system for freelancers, agencies, and businesses. It centralizes client context, marketing analytics, AI-driven strategy/content/lead workflows, and (later) platform integrations with approval-gated automation.

## Design Principles

1. **Tenant isolation first** — every business entity is scoped by `organization_id` (and usually `client_id`).
2. **Provider abstraction** — AI, storage, secrets, and integrations are swappable behind interfaces.
3. **Modular AI agents** — no monolithic prompts; orchestrated specialists with structured I/O.
4. **Demo vs live separation** — demo/seed data is explicitly flagged; never presented as live API success.
5. **Phase-gated delivery** — Phase 1 ships core OS; integrations and automation come later.
6. **Clean architecture** — API → Service → Repository → Model; UI → hooks/API client → components.

## High-Level Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                        Next.js Web App                          │
│  App Router · Design System · Client Workspaces · Charts        │
└────────────────────────────┬────────────────────────────────────┘
                             │ REST (JWT)
┌────────────────────────────▼────────────────────────────────────┐
│                         FastAPI API                             │
│  Auth · Tenancy · Rate limit · Audit · Validation               │
├─────────────────────────────────────────────────────────────────┤
│ Services          │ AI Orchestrator      │ Integrations Layer   │
│ Clients, Leads,   │ AnalyticsAgent       │ Meta / Google / IG   │
│ Content, Strategy │ StrategyAgent        │ YouTube / WhatsApp   │
│ Reports, CRM      │ ContentAgent         │ GA4 (Phase 3+)       │
│                   │ AdsAgent / LeadAgent │                      │
│                   │ ReportAgent          │                      │
├───────────────────┴──────────────────────┴──────────────────────┤
│ Repositories / Data Access                                      │
├─────────────────────────────────────────────────────────────────┤
│ PostgreSQL          │ Object Storage (S3-compatible abstraction) │
│ Encrypted secrets   │ Redis (rate limit / queues — later)       │
└─────────────────────────────────────────────────────────────────┘
```

## Multi-Tenancy Model

| Layer | Scope |
|-------|--------|
| Organization | Billing tenant, members, subscription |
| Organization Member | User ↔ org role (`owner`, `admin`, `member`) |
| Client | Marketing client under an organization |
| Client User | Optional client-level access (Phase 1: org members access all clients) |

**Isolation rule:** every query filters by `organization_id` from the authenticated session. Client-scoped resources also filter by `client_id`.

## Folder Structure

```
growthos-ai/
├── apps/
│   ├── api/                 # FastAPI backend
│   │   ├── app/
│   │   │   ├── api/v1/      # Route handlers
│   │   │   ├── core/        # Config, security, deps
│   │   │   ├── db/          # Session, base, migrations
│   │   │   ├── models/      # SQLAlchemy models
│   │   │   ├── schemas/     # Pydantic schemas
│   │   │   ├── repositories/
│   │   │   ├── services/
│   │   │   ├── ai/          # Provider + agents + orchestrator
│   │   │   ├── integrations/# Platform adapters
│   │   │   ├── storage/     # Object storage abstraction
│   │   │   ├── security/    # Secrets, audit, rate limit
│   │   │   └── demo/        # Demo mode seed helpers
│   │   ├── alembic/
│   │   ├── tests/
│   │   └── requirements.txt
│   └── web/                 # Next.js frontend
│       ├── src/
│       │   ├── app/         # Routes
│       │   ├── components/  # UI + domain components
│       │   ├── lib/         # API client, utils
│       │   ├── hooks/
│       │   ├── types/
│       │   └── styles/
│       └── package.json
├── docs/
├── docker-compose.yml
├── .env.example
└── README.md
```

## Database Schema (Phase 1 + forward-compatible)

Core tables (all tenant-aware where applicable):

- `users`, `organizations`, `organization_members`
- `clients`, `client_users`
- `social_accounts`, `ad_accounts`, `campaigns`, `ad_sets`, `ads`
- `social_posts`, `content_calendar`, `content_assets`
- `leads`, `lead_activities`
- `strategies`, `strategy_actions`
- `analytics_daily`, `analytics_campaign`
- `competitors`, `ai_recommendations`, `ai_conversations`
- `reports`, `integrations`, `notifications`, `subscriptions`
- `audit_logs`

See `docs/DATABASE.md` for column-level detail.

## API Structure

Base: `/api/v1`

| Area | Prefix | Phase |
|------|--------|-------|
| Auth | `/auth` | 1 |
| Organizations | `/organizations` | 1 |
| Clients | `/clients` | 1 |
| Dashboard | `/dashboard` | 1 |
| Strategies | `/clients/{id}/strategies` | 1 |
| Content | `/clients/{id}/content` | 1 |
| Leads | `/clients/{id}/leads` | 1 |
| AI Assistant | `/clients/{id}/assistant` | 1 (basic) |
| Analytics | `/clients/{id}/analytics` | 2 |
| Reports | `/clients/{id}/reports` | 2 |
| Recommendations | `/clients/{id}/recommendations` | 2 |
| Competitors | `/clients/{id}/competitors` | 2 |
| Integrations | `/integrations` | 3–4 |
| Campaigns | `/campaigns`, `/clients/{id}/campaigns` | 4 |

## AI Architecture

```
AIOrchestrator
  ├── AnalyticsAgent   — metrics interpretation (no invented numbers)
  ├── StrategyAgent    — situation → actions with approval states
  ├── ContentAgent     — platform content generation
  ├── AdsAgent         — ad creative / bid suggestions (Phase 2+)
  ├── LeadAgent        — scoring + CRM insights
  └── ReportAgent      — weekly narrative reports (Phase 2)
```

Each agent:

1. Receives a typed context + request schema
2. Calls `AIProvider` (OpenAI / Anthropic / Mock)
3. Returns structured JSON validated by Pydantic
4. Never invents metrics; returns `"Insufficient data."` when needed

`AIProvider` interface:

```python
class AIProvider(Protocol):
    async def complete(self, messages: list[Message], *, schema: type[BaseModel] | None = None) -> AIResponse: ...
```

## Integration Architecture

```python
class MarketingIntegration(Protocol):
    provider: str
    async def get_connection_status(self, org_id, client_id) -> ConnectionStatus
    async def sync(self, org_id, client_id) -> SyncResult
```

Statuses: `connected` | `not_connected` | `demo_data` | `sync_error`

Phase 1 ships stubs + demo analytics only when `DEMO_MODE=true`.

## Security

- JWT access + refresh tokens
- Password hashing (bcrypt)
- Organization membership authorization on every mutating route
- Secrets stored via `SecretStore` abstraction (Fernet local / KMS later)
- Platform tokens never returned to frontend
- Rate limiting middleware hooks (in-memory Phase 1; Redis later)
- Audit log for auth, client CRUD, strategy approvals

## Frontend Design System

Direction: professional agency OS — deep charcoal + warm accent (amber/copper), not purple-gradient SaaS cliché.

- Fonts: `Instrument Sans` (UI) + `Fraunces` (display)
- Tokens: CSS variables for color, radius, elevation, spacing
- Components: Button, Input, Select, Card, Table, Badge, Tabs, Modal, EmptyState, Skeleton, Chart wrappers, AI panel
- Layout: collapsible sidebar, top client context bar in workspaces

## Demo Mode

When `DEMO_MODE=true` (or org flag):

- Seed clients, campaigns, leads, analytics
- UI badges show **Demo Data**
- Integration cards show **Demo Data** / **Not Connected**, never fake Connected

## Extensibility Hooks (future phases)

- New AI agent = implement BaseAgent + register with Orchestrator
- New integration = implement MarketingIntegration + register factory
- Automation (Phase 5) = ActionExecutor consuming `Approved` strategy_actions

## Analytics ingestion

Normalized Meta/Google Ads daily performance is stored in
`marketing_performance_daily` and ingested via JobQueue (`analytics.ingest`).
See [ANALYTICS_INGESTION.md](./ANALYTICS_INGESTION.md).

## AI performance intelligence

Deterministic comparison + signal detection over ingested performance produces
analysis-only `performance_recommendations` (`analytics.analyze`). Lifecycle
approval alone does not mutate platforms. See
[AI_PERFORMANCE_INTELLIGENCE.md](./AI_PERFORMANCE_INTELLIGENCE.md).

## Closed-loop optimization

When `OPTIMIZATION_ENABLED=true`, recommendations flow through
`app/optimization/` (decision → policy → MANUAL / APPROVAL_REQUIRED / AUTONOMOUS)
and create `AIAction` rows only via existing `ActionService` / `ExecutionEngine`.
Wired into scheduled autopilot cycles; defaults remain safe (`false`). See
[AUTONOMOUS_MARKETING_ENGINE.md](./AUTONOMOUS_MARKETING_ENGINE.md).

## Operator control & production safety (Milestone 4)

Operator APIs under `/api/v1/autopilot/operator/*` and UI under `/autopilot/operator`.
Layered kill switches / canary allowlists default OFF. Manual UNKNOWN reconciliation
and legacy EXECUTING recovery are operator-driven (never auto re-execute).
Canary procedure: [PRODUCTION_CANARY.md](./PRODUCTION_CANARY.md).

## Provider verification Phase 1 (Milestone 5)

Read-only Meta/Google preflight + verification. Never mutates ads or enables autonomy.
See [PROVIDER_VERIFICATION.md](./PROVIDER_VERIFICATION.md).
**Verified ≠ autonomous spend enabled.**

## Controlled live canary Phase 2 (Milestone 5)

Authoritative gate in `app/automation/canary.py`. Dry-run and execute go through
**existing** ActionService → ExecutionEngine only (no second scheduler/engine).
Empty `CANARY_ALLOWED_*` allowlists deny all. Stale verification
(`PROVIDER_VERIFICATION_MAX_AGE_HOURS`) blocks live canary.
Confirm: `I_CONFIRM_CANARY_LIVE_PROVIDER_EXECUTION`.
**Canary success ≠ unrestricted production autonomy.**
Procedure: [PRODUCTION_CANARY.md](./PRODUCTION_CANARY.md).

## Real Meta verification (Milestone 6)

- Long-lived Meta token exchange + `ensure_meta_access_token` (`app/integrations/meta_oauth.py`)
- OAuth callback discovers ad accounts (`act_*`) into `Integration.config`
- Graph error classification via `classify_meta_graph_error`
- Pause / resume / budget still via `AdsExecutor` + `AdsReconciler` only
- Live execution defaults remain OFF; real Meta canary is manual
- Docs: [PROVIDER_VERIFICATION.md](./PROVIDER_VERIFICATION.md)

## Real Google verification (Milestone 7)

- Customer discovery persistence (`google_ads_discovery.py`) + refresh via `ensure_access_token`
- Campaign status mutate via `customers/{id}/campaigns:mutate`
- Error classification via `classify_google_ads_error`
- Budget mutate remains **UNSUPPORTED** (Google `campaignBudget` resource)
- Live execution defaults remain OFF; real Google canary is manual
- Docs: [PRODUCTION_CANARY.md](./PRODUCTION_CANARY.md) (Real Google checklist)
