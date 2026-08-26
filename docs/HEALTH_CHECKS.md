# Health checks and startup

Three endpoints, none of them authenticated, none behind the `/api/v1` prefix —
infrastructure should not have to track an API version to probe a pod.

| Endpoint | Question it answers | Who calls it |
|---|---|---|
| `GET /health/live` | Is this process wedged? | Orchestrator liveness probe, Docker `HEALTHCHECK` |
| `GET /health/ready` | Should this instance get traffic? | Orchestrator readiness probe, load balancer |
| `GET /health` | Legacy, kept for existing probes | Older deployments |

## Why these are separate

They have different consequences, and conflating them causes an outage rather
than preventing one.

A failing **liveness** probe gets the container killed and restarted. It
therefore checks nothing external. If liveness checked the database, a database
outage would restart every API pod in a rolling loop — and because restarting an
API pod does nothing to fix a database, the loop continues after the database
recovers, having also thrown away every warm connection pool.

A failing **readiness** probe removes the pod from the load balancer and leaves
it running. That is the correct response to a dependency being down: stop sending
this instance work, keep it alive so it can rejoin when the dependency returns.

## What readiness checks

| Check | What it proves | Required |
|---|---|---|
| `database` | `SELECT 1` on the async engine completes | always |
| `configuration` | The P0 startup guards still pass | always |
| `queue` | The `background_jobs` table is queryable, so a worker's claim query can run | always |
| `object_storage` | The configured backend is reachable — for S3, that the bucket answers | always |
| `rate_limit_backend` | Redis responds to `PING` | production only |

Redis is required for readiness only in production. Outside production the
in-process limiter is a legitimate choice, so a missing Redis is reported in the
`degraded` list without pulling the instance out of the pool. In production the
API already refuses to boot without `REDIS_URL` (P1-1), because per-process
counters hand an attacker N times the budget on an N-instance deployment.

Checks run concurrently, each with its own 3-second timeout. A probe that hangs
is indistinguishable from a dead process to most orchestrators, so one wedged
dependency must not be able to hang the probe itself.

Failure details are the exception *type* only. Connection errors routinely carry
a DSN with credentials in them, and this endpoint is unauthenticated. The full
error, with its traceback, goes to the log under `event: health.check_failed`.

## Response shape

Readiness returns 200 with `"status": "ready"`, or 503 with
`"status": "not_ready"`. Either way the body lists **every** check, so an
operator can see which dependency is down without opening the logs:

```json
{
  "status": "not_ready",
  "environment": "production",
  "degraded": null,
  "checks": {
    "database":            { "status": "ok",     "duration_ms": 3 },
    "configuration":       { "status": "ok",     "duration_ms": 0 },
    "queue":               { "status": "ok",     "duration_ms": 2 },
    "object_storage":      { "status": "failed", "duration_ms": 3001,
                             "detail": "Timed out after 3s." },
    "rate_limit_backend":  { "status": "ok",     "duration_ms": 1 }
  }
}
```

## Kubernetes

```yaml
livenessProbe:
  httpGet: { path: /health/live, port: 8000 }
  initialDelaySeconds: 15
  periodSeconds: 20
  timeoutSeconds: 3
  failureThreshold: 3

readinessProbe:
  httpGet: { path: /health/ready, port: 8000 }
  initialDelaySeconds: 5
  periodSeconds: 10
  timeoutSeconds: 5
  failureThreshold: 2
```

Set `failureThreshold` on liveness higher than on readiness. Readiness should
react quickly — pulling a struggling instance out of rotation is cheap and
reversible. Liveness should be slow to fire, because it is destructive.

The worker (`python -m app.worker`) has no HTTP surface. Monitor it through the
job metrics rather than a probe: a worker that has stopped claiming shows up as
jobs ageing in `QUEUED`, and expired leases are recovered by any other worker.

## Startup sequence

1. `configure_logging()` — structured JSON in production, human-readable
   otherwise, so a failure during the next step is still readable.
2. `validate_configuration()` — the P0 guards. **Production refuses to boot** on
   a missing secret, a mock AI provider, local file storage, an absent
   `REDIS_URL`, or inline job execution. Failing here is deliberate: a
   silently-degraded production instance is worse than one that will not start.
3. Development only: `create_all` plus SQLite column patching. Production applies
   Alembic migrations instead, as a separate step before the new image rolls out
   (`apps/api/scripts/migrate.sh`).

Readiness will fail until step 2 has passed, so an instance that boots into a bad
configuration never receives traffic.
