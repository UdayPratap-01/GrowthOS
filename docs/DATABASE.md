# GrowthOS AI — Database Schema

PostgreSQL with UUID primary keys, `created_at` / `updated_at` timestamps, and soft-delete where useful (`archived_at`).

## Entity Relationships (simplified)

```
organizations ──< organization_members >── users
organizations ──< clients
clients ──< strategies ──< strategy_actions
clients ──< leads ──< lead_activities
clients ──< content_calendar / content_assets / social_posts
clients ──< campaigns ──< ad_sets ──< ads
clients ──< analytics_daily / analytics_campaign
clients ──< competitors / ai_recommendations / ai_conversations / reports
organizations ──< integrations / subscriptions / notifications
```

## Tenant Isolation Indexes

Every tenant table includes:

- `organization_id UUID NOT NULL REFERENCES organizations(id)`
- Index: `(organization_id, ...)` for list queries
- Client tables also: `(organization_id, client_id)`

## Tables

### users
- id, email (unique), hashed_password, full_name, is_active, last_login_at, created_at, updated_at

### organizations
- id, name, slug (unique), demo_mode (bool), plan, created_at, updated_at

### organization_members
- id, organization_id, user_id, role (owner|admin|member), created_at
- unique(organization_id, user_id)

### clients
- id, organization_id, business_name, industry, website, description, location
- target_audience, products_services, marketing_goals, monthly_budget
- brand_voice, competitors (jsonb), primary_channels (jsonb), kpis (jsonb)
- status (active|archived), archived_at, created_at, updated_at

### client_users
- id, organization_id, client_id, user_id, role, created_at

### social_accounts / ad_accounts
- id, organization_id, client_id, provider, external_id, name
- connection_status, encrypted_credentials_ref, meta (jsonb), last_synced_at

### campaigns / ad_sets / ads
- Standard hierarchy with spend, status, platform refs, metrics jsonb

### social_posts / content_calendar / content_assets
- Platform content artifacts, schedule, status, asset URLs (storage keys)

### leads
- id, organization_id, client_id, name, email, phone, source, campaign, ad
- lead_score, score_explanation (jsonb), status, notes
- created_at, last_activity_at

### lead_activities
- id, lead_id, organization_id, client_id, activity_type, body, meta, created_at

### strategies / strategy_actions
- strategies: situation, problems, opportunities, strategy_summary, status, source
- strategy_actions: action, channel, objective, priority, effort, expected_outcome,
  required_assets, deadline, status (pending|approved|rejected|completed)

### analytics_daily / analytics_campaign
- date, metrics jsonb, source (demo|live), spend, leads, revenue, cpl, ctr, cvr

### competitors
- name, url, notes, observations jsonb

### ai_recommendations
- title, problem, evidence, recommendation, priority, expected_impact, status

### ai_conversations
- client_id, messages jsonb, agent, created_at

### reports
- period_start/end, content jsonb, export_path, status

### integrations
- provider, status, config jsonb (non-secret), secret_ref

### notifications / subscriptions / audit_logs
- operational + billing + security trails

## Migrations

Managed with Alembic under `apps/api/alembic`.
