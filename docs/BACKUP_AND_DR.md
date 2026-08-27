# Backups and disaster recovery

GrowthOS does not ship a cloud-vendor backup agent. Production operators must
configure managed Postgres backups and verify restore. Untested backups are
**PENDING**, not PASS.

---

## Database backups

| Item | Requirement |
|------|-------------|
| Frequency | At least daily full + continuous WAL / PITR where the host supports it |
| Retention | ≥ 7 days (30+ recommended) |
| Encryption | At rest via provider; in transit via TLS |
| Location | Separate from primary failure domain |
| Monitoring | Alert on failed backup jobs |

Example logical dump (ad-hoc / migration safety):

```bash
pg_dump "$DATABASE_URL_SYNC" -Fc -f "growthos-$(date -u +%Y%m%dT%H%M%SZ).dump"
```

Take a dump **before** applying risky migrations (see [`MIGRATIONS.md`](MIGRATIONS.md)).

---

## Object storage

- Enable **versioning** (or object-lock) on the media/report bucket.
- Restrict public write; use private buckets + authenticated/presigned access.
- Document lifecycle rules (cost control) without silently deleting active creatives.

---

## Restore procedure

```text
1. Provision empty Postgres (or restore into a new instance).
2. Restore backup / PITR to the target time.
3. Point DATABASE_URL / DATABASE_URL_SYNC at the restored instance (secrets only).
4. cd apps/api && alembic upgrade head && alembic check
5. Start API + worker against the restored DB.
6. Verify /health/ready, login, one org's clients/assets.
7. Confirm AUTONOMOUS_EXECUTION_ENABLED=false unless intentionally re-enabled.
```

**Never** `DROP DATABASE` / reset migrations as a “fix”.

### Restore test status

| Environment | Result |
|-------------|--------|
| Local / staging restore drill | **PENDING** (operator must execute before claiming PASS) |
| Production restore drill | **PENDING** |

Until a restore drill has evidence, report **Restore: PENDING**.

---

## Recovery objectives (targets — tune per SLA)

| Metric | Initial target |
|--------|----------------|
| RPO | ≤ 24h (better with PITR) |
| RTO | ≤ 4h for API+DB restore to ready |

---

## Incident scenarios

| Scenario | Detection | Immediate action | Recovery | Verify |
|----------|-----------|------------------|----------|--------|
| Database failure | Ready probe / host alerts | Failover or restore; keep kill switch available | Restore + migrate | `/health/ready`, sample reads |
| Worker failure | Queue backlog / job age | Restart workers; do not duplicate-mutate | Scale / fix handler | Jobs complete once |
| Storage failure | Ready probe / `STORAGE_*` errors | Fail closed on media; no fake COMPLETED | Fix bucket/creds | Upload+download |
| Provider outage | Provider errors / rate limits | Stop canary; kill switch if needed | Wait / rotate tokens | Read-only verify |
| Credential compromise | Auth anomalies / leaked secret | Rotate per [`SECRET_ROTATION.md`](SECRET_ROTATION.md); revoke sessions | Re-consent OAuth if ENCRYPTION_KEY rotated | Login + integrations |
| Application outage | 5xx / liveness | Rollback image | Redeploy last known good | Smoke checklist |
| Bad deploy | Ready fail / error spike | Rollback app; forward-fix DB if needed | Prior image | Smoke + security smoke |

---

## Related

- [`PRODUCTION_RUNBOOK.md`](PRODUCTION_RUNBOOK.md)
- [`MIGRATIONS.md`](MIGRATIONS.md)
- [`HEALTH_CHECKS.md`](HEALTH_CHECKS.md)
