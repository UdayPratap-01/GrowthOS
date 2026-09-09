# GrowthOS — Final Production Readiness

**Honest status (do not inflate):**

```text
M5 Phase 2  = CODE COMPLETE
M6 Meta     = CODE COMPLETE / REAL META VERIFICATION PENDING
M7 Google   = CODE COMPLETE / REAL GOOGLE VERIFICATION PENDING
M8 Deploy   = CODE/INFRASTRUCTURE READY / HOSTED DEPLOYMENT PENDING
```

```text
LIVE ADS = OFF
AUTONOMOUS MUTATIONS = OFF
```

Checkpoints on `main`: M5 `5f377d6` · M6 `5b6e256` · M7 `bb586d1` · M8 `c8ca0ed`

Execution path (single — do not fork):

```text
ActionService → ExecutionEngine → AdsExecutor → AdsReconciler
```

---

## 1. Completed

- Auth (JWT + refresh rotation, httpOnly cookie), RBAC, tenant isolation
- Rate limiting (Redis-backed in production), security headers, CORS guards
- Startup fail-fast for production placeholders / DEMO / mock AI / SQLite / local storage
- Job worker + leases; health live/ready; `/metrics` with token
- Closed-loop optimizer (gated OFF), kill switch, UNKNOWN reconciliation
- M5 canary allowlists, confirm phrases, limits (defaults deny)
- M6 Meta OAuth, long-lived tokens, encrypted storage, discovery, pause/resume, verify, reconcile
- M7 Google OAuth, customer/campaign discovery, pause/resume, verify, reconcile
- Operator UI readiness banners + META/GOOGLE stage matrix
- CI: pytest + alembic check + web typecheck/lint/build
- Production compose template, runbooks, backup/secret-rotation docs

## 2. Code complete (awaiting credentials or hosting)

| Area | Status |
|------|--------|
| Meta live OAuth + read-only verify + canary | Needs Meta App + test account |
| Google live OAuth + read-only verify + canary | Needs Google OAuth + Ads developer token |
| Hosted Postgres / Redis / S3 / TLS / domain | Needs infrastructure |
| CD pipeline / restore drill / on-call | Needs ops |
| Real PSP billing | Optional for ads-off platform launch |

## 3. Credentials required

### Meta

- `META_APP_ID`, `META_APP_SECRET`
- OAuth user with access to a **test** ad account
- Test campaign (for canary allowlist)
- Redirect URI = `{API_PUBLIC_URL}/api/v1/integrations/meta/callback`

### Google

- `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`
- `GOOGLE_ADS_DEVELOPER_TOKEN`
- Optional `GOOGLE_ADS_LOGIN_CUSTOMER_ID` (MCC)
- OAuth user + Ads customer + test campaign
- Redirect URI = `{API_PUBLIC_URL}/api/v1/integrations/google_ads/callback`

**Deferred (documented, not faked):** Google `update_budget` remains **UNSUPPORTED**.

## 4. External configuration required

- Meta Developer App products/permissions + Valid OAuth Redirect URIs
- Google Cloud OAuth client + Google Ads API access / developer token approval path
- Canary env allowlists (`CANARY_ALLOWED_*`) — empty means **deny all**
- `TRUSTED_PROXY_IPS`, `API_CORS_ORIGINS`, `FRONTEND_URL`, `API_PUBLIC_URL` for production edge

## 5. Hosting required

- Managed PostgreSQL (TLS) + Redis + private object storage
- API + worker processes (`docker-compose.prod.yml` template)
- Web (Next standalone) with production `NEXT_PUBLIC_API_URL`
- Domain + TLS termination
- Metrics scrape + log sink + backups + restore drill
- Secret manager (no placeholders)

## 6. Exact final launch checklist

1. Fill production secrets (never commit `.env`)
2. `ENVIRONMENT=production`, `DEMO_MODE=false`, `STRICT_LIVE_MODE=true`
3. `AUTONOMOUS_EXECUTION_ENABLED=false`, `META_AUTONOMOUS_ENABLED=false`, `GOOGLE_AUTONOMOUS_ENABLED=false`
4. `OPTIMIZATION_ENABLED=false`, `CANARY_ENABLED=false`, `AUTONOMOUS_KILL_SWITCH=false`
5. Provision Postgres / Redis / S3; set `DATABASE_URL*`, `REDIS_URL`, storage vars
6. Run Alembic migrations; start API + worker; deploy web
7. Confirm `/health/ready` and CI green on the release commit
8. Staging smoke **with ads OFF**
9. Provide Meta credentials → connect → read-only verify → controlled canary
10. Provide Google credentials → connect → read-only verify → controlled canary
11. Only then consider enabling broader autonomy (still behind allowlists)
12. Enable backups; complete restore drill; wire monitoring/paging

## 7. Exact verification checklist

### Meta (M6)

1. Set Meta App credentials + redirect URI
2. Connect via Integrations → Meta OAuth
3. Operator → Refresh verification (confirm `I_CONFIRM_READ_ONLY_PROVIDER_VERIFICATION`)
4. Confirm `/me`, ad accounts, campaigns discovered; stage = VERIFIED
5. Configure `CANARY_ALLOWED_*` for **test** act_ + campaign only
6. Dry-run → Execute canary (`I_CONFIRM_CANARY_LIVE_PROVIDER_EXECUTION`) pause then resume
7. Confirm post-verify + reconciliation; resolve UNKNOWN if needed
8. Document evidence; keep autonomous latches OFF

### Google (M7)

1. Set Google OAuth + developer token (+ MCC if needed)
2. Connect Google Ads
3. Read-only verify; confirm customer + campaign discovery
4. Canary allowlists for test customer/campaign
5. Dry-run → pause/resume canary → reconcile
6. Do **not** expect budget mutate (UNSUPPORTED)

## 8. Exact commands

```bash
# Local seed (dev only — refused in production)
./scripts/seed-demo.sh
# or from repo root when API cwd is root:
PYTHONPATH=apps/api apps/api/.venv/bin/python -m app.demo.seed

# Backend tests
cd apps/api && PYTHONPATH=. .venv/bin/pytest -q

# Alembic
cd apps/api && PYTHONPATH=. .venv/bin/alembic check

# Frontend
cd apps/web && npm run typecheck && npm run lint && npm run build

# Production-shaped compose (fill secrets first)
docker compose -f docker-compose.prod.yml up -d
```

## 9. Exact environment variables (classification)

See root `.env.example` for full list. Summary:

| Class | Examples |
|-------|----------|
| **REQUIRED (prod)** | `ENVIRONMENT`, `SECRET_KEY`, `ENCRYPTION_KEY`, `DATABASE_URL`, `DATABASE_URL_SYNC`, `REDIS_URL`, storage (`STORAGE_BACKEND`/`S3_*`), `METRICS_TOKEN`, `API_CORS_ORIGINS`, `API_PUBLIC_URL`, `FRONTEND_URL`, `AI_PROVIDER` + key |
| **PRODUCTION ONLY** | `STRICT_LIVE_MODE=true`, `DB_AUTO_CREATE` unset/false, `INLINE_JOB_EXECUTION=false`, `TRUSTED_PROXY_IPS` |
| **DEVELOPMENT ONLY** | `DEMO_MODE=true`, `ALLOW_DEMO_SEED=true`, `AI_PROVIDER=mock`, SQLite URLs, `NEXT_PUBLIC_DEMO_*` |
| **PROVIDER-SPECIFIC** | `META_*`, `GOOGLE_*`, `GOOGLE_ADS_*`, `CANARY_*`, `PROVIDER_VERIFICATION_*` |
| **OPTIONAL** | Sentry, PSP, image/video providers, scheduler toggles |

## 10. What must NEVER be enabled before launch

- `DEMO_MODE=true` with `ENVIRONMENT=production`
- `AI_PROVIDER=mock` in production
- `AUTONOMOUS_EXECUTION_ENABLED=true` before completed Meta/Google canaries
- `META_AUTONOMOUS_ENABLED` / `GOOGLE_AUTONOMOUS_ENABLED` without allowlists + evidence
- `OPTIMIZATION_ENABLED=true` driving live mutations without verification
- `CANARY_ENABLED=true` aimed at production spend accounts
- Empty canary allowlists treated as allow-all (they are **deny**)
- Claiming META/GOOGLE **VERIFIED** from health `CONFIGURED` alone
- Implementing a second ads mutation path outside ActionService → ExecutionEngine → AdsExecutor

## Related docs

- [`PROVIDER_VERIFICATION.md`](PROVIDER_VERIFICATION.md)
- [`PRODUCTION_CANARY.md`](PRODUCTION_CANARY.md)
- [`PRODUCTION_READINESS.md`](PRODUCTION_READINESS.md)
- [`PRODUCTION_RUNBOOK.md`](PRODUCTION_RUNBOOK.md)
- [`BACKUP_AND_DR.md`](BACKUP_AND_DR.md)
- [`SECRET_ROTATION.md`](SECRET_ROTATION.md)
