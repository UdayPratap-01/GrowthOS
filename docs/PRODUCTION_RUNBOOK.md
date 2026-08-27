# GrowthOS AI — Production Runbook

Operator guide for controlled production deployment. Live Meta/Google ad mutations
remain **OFF** unless an explicit canary has passed (see
[`PRODUCTION_CANARY.md`](PRODUCTION_CANARY.md)).

---

## Safety defaults (do not change casually)

| Variable | Production default |
|----------|-------------------|
| `DEMO_MODE` | `false` |
| `AUTONOMOUS_EXECUTION_ENABLED` | `false` |
| `CANARY_ENABLED` | `false` |
| `PROVIDER_VERIFICATION_ENABLED` | `false` |
| `INLINE_JOB_EXECUTION` | `false` |
| `DB_AUTO_CREATE` | `false` |
| `STORAGE_BACKEND` | `s3` (or compatible) |

**Production deploy ≠ live ads enabled.**

---

## Deploy

1. Put secrets in the host secret manager / `.env.production` (never commit).
2. Apply migrations: `cd apps/api && ./scripts/migrate.sh`  
   or `docker compose -f docker-compose.prod.yml --env-file .env.production run --rm migrate`
3. Deploy API image (`uvicorn app.main:app`) and worker (`python -m app.worker`).
4. Deploy frontend (Vercel or `apps/web` production build) with production `NEXT_PUBLIC_API_URL`.
5. Confirm probes:
   - `GET /health/live` → 200
   - `GET /health/ready` → 200 (DB, storage, Redis, config)

Rollback previous application image if readiness fails.

---

## Rollback

| Layer | Action |
|-------|--------|
| Application / worker | Redeploy previous container image tag |
| Frontend | Redeploy previous build / previous Vercel deployment |
| Configuration | Restore previous env / secret versions |
| Database | Prefer **forward-fix**. Do not `alembic downgrade` in production unless the migration was designed for it. Restore from backup if schema must rewind (see [`BACKUP_AND_DR.md`](BACKUP_AND_DR.md)). |

---

## Restart API

```bash
# compose template
docker compose -f docker-compose.prod.yml --env-file .env.production restart api

# or restart the process/pod on your host
```

Confirm `/health/live` then `/health/ready`.

---

## Restart workers

```bash
docker compose -f docker-compose.prod.yml --env-file .env.production restart worker
```

Workers finish the current claimed job before stopping (SIGTERM). A restart must
not duplicate external mutations — jobs use leases + idempotency keys.

---

## Check health

```bash
curl -sS https://api.example.com/health/live
curl -sS https://api.example.com/health/ready
```

Liveness = process up. Readiness = dependencies usable. See [`HEALTH_CHECKS.md`](HEALTH_CHECKS.md).

---

## Check queue

- Metrics: scrape `/metrics` with `Authorization: Bearer $METRICS_TOKEN` (or the project's configured metrics auth).
- DB: inspect `background_jobs` for `queued` / `running` / `failed` / lease expiry.
- Operator UI: failed jobs / action history where available.

If backlog grows: scale workers, inspect handler errors, confirm Redis and DB healthy.

---

## Check database

```bash
# connectivity (from a bastion / migrate container)
psql "$DATABASE_URL_SYNC" -c 'select 1'
cd apps/api && alembic current && alembic check
```

Never run `DROP DATABASE`, `TRUNCATE`, or reset migrations as part of normal deploy.

---

## Check storage

Confirm `STORAGE_BACKEND` is S3-compatible and `S3_BUCKET` exists. Upload a small
object via a media generation job (or storage health on readiness) and download
it through the authenticated media endpoint after an API restart.

---

## Activate kill switch

Stops **new** autonomous / canary mutations. Does not delete recommendations.

```bash
# Platform secret / env
AUTONOMOUS_KILL_SWITCH=true
# Restart API + worker so all processes pick it up
```

Also set `CANARY_ENABLED=false` and/or `AUTONOMOUS_EXECUTION_ENABLED=false` for
a harder stop. Who: platform operators with access to production secrets.
Effect: next request/worker cycle — no new external ad mutations.

---

## Disable autonomy

```text
AUTONOMOUS_EXECUTION_ENABLED=false
META_AUTONOMOUS_ENABLED=false
GOOGLE_AUTONOMOUS_ENABLED=false
OPTIMIZATION_ENABLED=false
CANARY_ENABLED=false
```

Restart API and workers.

---

## Rotate credentials

See [`SECRET_ROTATION.md`](SECRET_ROTATION.md).

---

## Restore backup

See [`BACKUP_AND_DR.md`](BACKUP_AND_DR.md). After restore: `alembic upgrade head`,
restart API/worker, verify `/health/ready`, spot-check one org's data.

---

## Inspect failed actions

1. Operator / autopilot action history for the organization.
2. Audit / structured logs filtered by `action_id`, `organization_id`, `request_id`.
3. Provider error category (auth, rate limit, reconciliation) — never treat HTTP
   accept alone as success.

---

## Inspect reconciliation failures

1. Find action with reconciliation failed / remote state mismatch.
2. Re-read remote campaign state (read-only verify) — do **not** blind-retry
   UNKNOWN mutations.
3. Resolve via operator decision; keep kill switch available.

---

## Smoke test (no live ads)

```text
Homepage → Login → Organization → Client → Dashboard
→ /health/live + /health/ready
→ Worker drains a non-mutating job (e.g. report) if configured
→ Storage read of an existing asset
→ AI generation only if providers configured (optional)
→ Confirm AUTONOMOUS_EXECUTION_ENABLED and CANARY_ENABLED still false
```

Do **not** run real Meta/Google mutations as part of a generic smoke test.

---

## Security smoke

| Check | Expected |
|-------|----------|
| No auth | 401 |
| Cross-tenant id | 404/403 |
| Invalid/expired token | 401 |
| Viewer on mutate | 403 |
| Kill switch on | Mutation blocked; provider not called |
| Bad provider credentials | Safe failure, no fake success |

---

## Related docs

- [`../PRODUCTION_READINESS.md`](../PRODUCTION_READINESS.md) — readiness matrix
- [`PRODUCTION_CANARY.md`](PRODUCTION_CANARY.md) — live provider canary
- [`PROVIDER_VERIFICATION.md`](PROVIDER_VERIFICATION.md)
- [`MIGRATIONS.md`](MIGRATIONS.md)
- [`SECRET_ROTATION.md`](SECRET_ROTATION.md)
- [`BACKUP_AND_DR.md`](BACKUP_AND_DR.md)
