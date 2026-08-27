# Production TODO — Milestone 8 follow-ups

Historical P0–P3 detail remains in [`../PRODUCTION_TODO.md`](../PRODUCTION_TODO.md).
This file tracks what remains after M8 code/infrastructure packaging.

---

## Closed in M8 (code / docs)

| Item | Evidence |
|------|----------|
| CI pipeline | `.github/workflows/ci.yml` |
| Security headers | `SecurityHeadersMiddleware` |
| Non-root API image | `apps/api/Dockerfile` `USER growthos` |
| Prod-shaped compose template | `docker-compose.prod.yml` |
| Runbook / DR / secret rotation | `docs/PRODUCTION_RUNBOOK.md`, `BACKUP_AND_DR.md`, `SECRET_ROTATION.md` |
| Readiness matrix | `docs/PRODUCTION_READINESS.md` |

Live ads defaults remain **OFF** (`AUTONOMOUS_EXECUTION_ENABLED`, `CANARY_ENABLED`).

---

## Still open (ops / product)

| ID | Item | Blocker type |
|----|------|--------------|
| M8-OPS-1 | Provision managed Postgres, Redis, S3 with TLS | Ops |
| M8-OPS-2 | Load production secrets (rotate placeholders) | Ops — P0-10 |
| M8-OPS-3 | Domain + TLS + OAuth production redirect URIs | Ops |
| M8-OPS-4 | Enable DB backups + **restore drill** | Ops — P2-10 |
| M8-OPS-5 | Wire metrics scraping + paging alerts | Ops |
| M8-OPS-6 | Staging smoke (no live ads) | Ops |
| M8-OPS-7 | CD deploy pipeline (host-specific) | Ops |
| P2-1 | Real payment provider | Product |
| P2-4/5 | Meta/Google real canary → live ads | Credentials + canary |
| P1-5 residual | Password reset / email verification | Product |

---

## Provider status

```text
META LIVE VERIFICATION = PENDING
GOOGLE LIVE VERIFICATION = PENDING
LIVE EXECUTION = OFF
```
