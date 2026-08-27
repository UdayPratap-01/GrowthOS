# Production readiness — Milestone 8 matrix

Companion to the historical audit in [`../PRODUCTION_READINESS.md`](../PRODUCTION_READINESS.md).
**Do not mark PASS without evidence.** Live Meta/Google canaries are separate gates.

**Checkpoint context:** M5 `5f377d6` · M6 `5b6e256` · M7 `bb586d1` · M8 (this work)

---

## Production readiness matrix

| Area | Status | Evidence | Blocker |
|------|--------|----------|---------|
| Application | **PASS** (code) | FastAPI + Next.js; 900+ backend tests | Real cloud deploy still operator-owned |
| Database | **PASS** (app) / **PENDING** (hosted) | Postgres + Alembic; compose prod template | Managed Postgres + TLS credentials |
| Migrations | **PASS** | `alembic upgrade head` + `alembic check` in CI | Destructive migrate discipline |
| Workers | **PASS** (code) | `python -m app.worker`; leases + tenant-scoped jobs | Deploy worker replicas |
| Storage | **PASS** (code) / **PENDING** (bucket) | S3 adapter + startup refuses `local` in prod | Provision private bucket |
| Authentication | **PASS** | Refresh rotation, httpOnly cookie, logout | Password reset still open (P2) |
| Authorization | **PASS** | RBAC `require_permission` + isolation tests | — |
| Secrets | **PASS** (guards) / **PENDING** (ops) | Startup rejects placeholders; [`SECRET_ROTATION.md`](SECRET_ROTATION.md) | Platform secret store values |
| Provider integrations | **CODE READY** | M6 Meta / M7 Google | Real canaries PENDING |
| Monitoring | **PASS** (code) | `/metrics`, structured logs, events | Wire scrapers + alerts |
| Logging | **PASS** | JSON + redaction + request_id | Log sink |
| Backups | **PENDING** | [`BACKUP_AND_DR.md`](BACKUP_AND_DR.md) | Enable + monitor backups |
| Restore | **PENDING** | Procedure documented | Restore drill evidence |
| CI/CD | **PASS** (CI) / **PENDING** (CD) | `.github/workflows/ci.yml` | Deploy pipeline to host |
| Domain | **PENDING** | Runbook documents requirements | DNS cutover |
| SSL | **PENDING** | HSTS header when not development | Edge TLS certificates |
| CORS | **PASS** | Explicit origins; prod rejects `*` | Set production frontend origin |
| Rate limiting | **PASS** | Redis-backed; trusted proxies | Set `TRUSTED_PROXY_IPS` |
| Billing | **PARTIAL** | Plans/quotas; no real PSP | Stripe/etc. for payments |
| Security | **PASS** (app) | Headers, guards, isolation tests | Edge WAF optional |
| Disaster recovery | **PARTIAL** | Documented scenarios | Drill evidence |
| Incident response | **PARTIAL** | Kill switch + runbook | On-call roster |

---

## Launch gates

### Required for production *deployment* (platform up, ads off)

- [x] Backend tests in CI
- [x] Frontend typecheck/lint/build in CI
- [x] Alembic check in CI
- [x] Security headers middleware
- [x] Non-root API container user
- [x] Production runbook / backup / secret-rotation docs
- [x] Live execution defaults OFF
- [ ] Secrets filled in host secret manager
- [ ] Managed Postgres + Redis + S3 provisioned
- [ ] Domain + TLS
- [ ] Backup enabled + restore drill
- [ ] Alerting wired
- [ ] Staging smoke (no live ads)

### Provider gates (not required to deploy the product platform)

| Provider | Code | Real verification |
|----------|------|-------------------|
| Meta | READY (M6) | **PENDING CREDENTIALS** |
| Google | READY (M7) | **PENDING CREDENTIALS** |

Therefore:

```text
LIVE ADS = OFF
AUTONOMOUS MUTATIONS = OFF
```

---

## Status vocabulary

| Claim | Meaning |
|-------|---------|
| **PRODUCTION DEPLOYMENT READY** | Hosted stack verified healthy with secrets, DB, workers, storage, TLS, backups |
| **PROVIDER LIVE VERIFICATION COMPLETE** | Real Meta/Google canaries passed |
| **FULL PRODUCTION LAUNCH READY** | Both of the above + billing/legal as required |

Current honest status:

```text
M8 CODE/INFRASTRUCTURE READY — PRODUCTION DEPLOYMENT PENDING
```

---

## Related

- [`PRODUCTION_RUNBOOK.md`](PRODUCTION_RUNBOOK.md)
- [`PRODUCTION_CANARY.md`](PRODUCTION_CANARY.md)
- [`../PRODUCTION_TODO.md`](../PRODUCTION_TODO.md)
