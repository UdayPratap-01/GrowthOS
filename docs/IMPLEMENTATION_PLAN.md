# GrowthOS AI — Implementation Plan

## Phase Overview

| Phase | Focus | Status |
|-------|-------|--------|
| **1** | Auth, tenancy, dashboard, clients, workspace, AI strategy, content studio, lead CRM | **Complete** |
| **2** | Analytics, reports, recommendations, lead scoring UI depth, competitors | **Complete** |
| **3** | Meta, Instagram, WhatsApp, Google Analytics integrations | **Complete** |
| **4** | Google Ads, YouTube, campaigns list API | **Complete** |
| **5** | Autopilot, approvals, execution, optimization loops | **Complete** |

## Phase 1 Deliverables

### Backend
1. FastAPI app with `/api/v1` routers
2. SQLAlchemy models + Alembic migration for full forward schema
3. JWT auth (register/login/me) + org membership
4. Client CRUD + archive + search/filter
5. Dashboard aggregate metrics (demo-aware)
6. Strategy engine (StrategyAgent + approval workflow)
7. Content Studio generation + save + calendar entries
8. Lead CRM (CRUD, stages, kanban data, basic AI scoring)
9. AI provider abstraction (`mock` default, OpenAI/Anthropic optional)
10. Integration stubs with connection statuses
11. Seed script for demo org

### Frontend
1. Auth screens (login/register)
2. App shell with specified sidebar navigation
3. Dashboard with KPIs, AI priorities, client cards, approvals
4. Clients list + create/edit + workspace tabs
5. AI Strategy page with action approval
6. Content Studio generator + saved content + calendar
7. Leads table + kanban
8. Placeholder routes for Phase 2+ pages (Analytics, Reports, etc.) with clear empty/coming-soon states
9. Design system components

### Ops
1. `docker-compose.yml` (Postgres)
2. `.env.example`
3. README setup instructions
4. API smoke tests

## Phase 1 Non-Goals

- Real Meta/Google OAuth connections
- PDF export (stub button OK)
- Live analytics sync
- Automated execution of approved actions
- Billing provider

## Implementation Order (Phase 1)

1. Docs + repo structure ✅
2. Backend core (config, db, models, auth)
3. Client + dashboard APIs
4. AI layer + strategy/content/leads services
5. Seed + tests
6. Frontend scaffold + design system
7. Wire pages to API
8. End-to-end local run verification

## Testing Strategy

- Unit: AI schema validation, lead score explanation rules
- API: auth, tenant isolation, client CRUD, strategy approval
- Manual: login → create client → generate strategy → content → lead board

## Definition of Done (Phase 1)

- `docker compose up -d` starts Postgres
- API runs on `:8000`, web on `:3000`
- Demo login works with seeded data
- All Phase 1 screens functional
- No page claims a live integration is connected unless it is

## Phase 5 Deliverables

### Backend
1. Autonomy settings (copilot / assisted / autonomous) with budget & rate limits
2. Structured `AIAction` system + action registry
3. Approval + Execution engines with audit + notifications
4. CreativeAgent, OptimizationAgent, MonitoringAgent
5. Image/Video provider abstractions (`none` | `demo`)
6. SocialPublisher adapters (honest not-connected / demo)
7. Optimization rules + controlled decision loop
8. DB-backed job queue + webhook signature validation
9. Object storage abstraction (local)

### Frontend
1. `/autopilot` dashboard
2. `/approvals` Approval Center
3. `/ai-activity` activity timeline
4. Autonomy settings in `/settings`

### Honesty
- No fake live publish / ads write success
- DEMO DATA labeled when simulated
- IMAGE/VIDEO GENERATION NOT CONFIGURED when providers unset

## Phase 4 Deliverables

### Backend
1. Google Ads OAuth + encrypted token vault + developer-token gated API sync
2. YouTube OAuth + channel discovery + Data API snapshot sync
3. Shared Google OAuth helpers (code exchange + refresh)
4. Sync writes live `AdAccount` / `Campaign` / `AnalyticsCampaign` / `AnalyticsDaily` / `SocialAccount`
5. `GET /campaigns` and `GET /clients/{id}/campaigns`

### Frontend
1. Integrations UI badges + setup notes for Ads/YouTube
2. `/campaigns` table with client/platform filters and demo vs live badges
3. Client workspace Campaigns tab lists synced rows

### Honesty rules
- Never mark Connected without OAuth + `secret_ref`
- Google Ads `can_connect` requires `GOOGLE_ADS_DEVELOPER_TOKEN`
- Empty API responses do not invent metrics
