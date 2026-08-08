# GrowthOS AI

AI-powered digital marketing operating system for freelancers, agencies, and businesses.

Phase 1 delivers authentication, multi-tenant organizations, dashboard, client management, client workspaces, AI provider abstraction, Strategy Engine, Content Studio, and Lead CRM.

## Architecture

See:

- [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md)
- [`docs/DATABASE.md`](docs/DATABASE.md)
- [`docs/IMPLEMENTATION_PLAN.md`](docs/IMPLEMENTATION_PLAN.md)
- [`docs/AUTONOMOUS_MARKETING_ARCHITECTURE.md`](docs/AUTONOMOUS_MARKETING_ARCHITECTURE.md)
- [`docs/AUTONOMOUS_MARKETING_IMPLEMENTATION_REPORT.md`](docs/AUTONOMOUS_MARKETING_IMPLEMENTATION_REPORT.md)

```
apps/
  api/   FastAPI + SQLAlchemy + AI agents
  web/   Next.js + TypeScript + Tailwind
```

## Prerequisites

- Node.js 20+ (for the web app)
- Docker Desktop (recommended if you don’t want to manage Python)

Python is optional. Prefer the Docker API path below.

## Quick start (no local Python)

### 1. Start API with Docker

Make sure Docker Desktop is running, then:

```bash
./scripts/start-api-docker.sh
```

This builds the API image, starts Postgres, seeds demo data, and serves:

- API: http://localhost:8000
- Docs: http://localhost:8000/docs

### 2. Start web (Node only)

```bash
./scripts/start-web.sh
```

Open http://localhost:3000

Demo login:

- Email: `demo@growthos.ai`
- Password: `demo1234`

## Alternative: local Python venv

Only if you want to run the API without Docker:

```bash
cd apps/api
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp ../../.env .env
PYTHONPATH=. python -m app.demo.seed
uvicorn app.main:app --reload --port 8000
```

## AI providers

Set in `.env`:

```bash
AI_PROVIDER=mock      # default for local/demo
# AI_PROVIDER=openai
# OPENAI_API_KEY=...
# AI_PROVIDER=anthropic
# ANTHROPIC_API_KEY=...
```

Business logic talks to `AIProvider` only. Agents never invent metrics; unavailable data returns **Insufficient data.**

## Integrations

Adapters: Meta, Instagram, WhatsApp, Google Analytics (Phase 3); Google Ads, YouTube (Phase 4).

Statuses are honest:

- `connected`
- `not_connected`
- `demo_data`
- `sync_error`

**Connected** is never claimed without a completed OAuth flow and encrypted tokens.

## Tests

```bash
cd apps/api
source .venv/bin/activate
PYTHONPATH=. pytest -q
```

## Phase roadmap

| Phase | Scope |
|------|--------|
| 1 | Auth, tenancy, dashboard, clients, strategy, content, leads |
| 2 | Analytics (7/30/90), weekly reports + PDF, recommendations workflow, lead scoring, competitors |
| 3 | Meta, Instagram, WhatsApp, GA OAuth + encrypted tokens + live sync |
| 4 | Google Ads, YouTube, campaigns list |
| 5 | Autopilot, approvals, execution engine, optimization loops |

### Phase 5 Autopilot + Autonomous Marketing Engine

Pages: `/autopilot`, `/campaign-builder`, `/creative-library`, `/approvals`, `/ai-activity`, autonomy controls in `/settings`.

API prefix: `/api/v1/autopilot/*` including:

- `POST /run` — one-click marketing autopilot with step progress
- `POST /campaigns/build` — AI Campaign Builder
- `GET /creative/library` — creative library
- settings, actions, approve/reject/execute, creative/image/video, optimization, decision-loop

Modes: **copilot** · **assisted** · **autonomous** — with budget/rate/platform safety limits.  
AI never executes free text; commands become structured `AIAction` records.  
Live publish/ads writes only after platform confirmation. Demo simulations are labeled **DEMO DATA**.

### DEMO vs LIVE mode

Effective mode = `organization.demo_mode` **OR** env `DEMO_MODE`.

| Mode | Behavior |
|------|----------|
| **DEMO** | Seed metrics OK; simulated executions clearly labeled DEMO DATA |
| **LIVE** | No silent demo fallback; KPI `data_source` is `live` or `mixed` if seed rows remain |
| **mixed** | Org wants live but demo seed analytics rows still exist |

Top bar always shows DEMO MODE or LIVE MODE. Toggle org flag in Settings (`PATCH /auth/organization/mode`). Set `DEMO_MODE=false` in `.env` for production.

Optional generation providers:

```bash
IMAGE_PROVIDER=none   # or demo (DEMO DATA concepts only)
VIDEO_PROVIDER=none   # or demo
```

### Phase 3–4 integrations

Set credentials in `.env` (never commit secrets):

```bash
META_APP_ID=...
META_APP_SECRET=...
GOOGLE_CLIENT_ID=...
GOOGLE_CLIENT_SECRET=...
GOOGLE_ADS_DEVELOPER_TOKEN=...   # required for Google Ads Connect/Sync
# GOOGLE_ADS_LOGIN_CUSTOMER_ID=  # optional MCC
API_PUBLIC_URL=http://127.0.0.1:8000
FRONTEND_URL=http://127.0.0.1:3000
```

Register redirect URIs for each provider, e.g.  
`{API_PUBLIC_URL}/api/v1/integrations/google_ads/callback` and `.../youtube/callback`.

Then open `/integrations` → Connect. Tokens are Fernet-encrypted (`ENCRYPTION_KEY`) in `integrations.secret_ref` and never sent to the browser.  
Without credentials, status stays **Demo Data** / **Not Connected** — never fake **Connected**.

Google Ads sync writes `AdAccount`, `Campaign`, `AnalyticsCampaign`, and `AnalyticsDaily` (`data_source=live`).  
YouTube sync writes channel `SocialAccount` + a live analytics snapshot (no fabricated history).

### Phase 2 / 4 pages

- `/analytics` — social/campaigns/leads/conversions charts + period comparison
- `/campaigns` — demo + live campaign table (Google Ads sync)
- `/reports` — AI weekly report generation + PDF export
- `/recommendations` — evidence-backed recommendations with approve/reject/save/complete
- `/lead-scoring` — score distribution + explanations
- `/competitors` — qualitative competitor tracking per client
