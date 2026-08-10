# GrowthOS AI — Production Readiness Audit

**Audit date:** 2026-08-10
**Auditor:** Lead Production Engineer
**Commit audited:** working tree at `/Users/udaypratapsingh/Downloads/growthos-ai`
**Method:** static code review of every module + live runtime testing against a running API (`127.0.0.1:8000`) and web app (`127.0.0.1:3000`)

> Every classification below was verified against the repository or against live API behavior. Nothing is marked working because a UI screen exists.

---

## 0. Hardening pass — 2026-08-10 (post-audit update)

**Verdict is unchanged: NOT READY for a payment-taking public launch** (billing
provider integration and live ad publishing remain P2). P0 critical risks and
P1 production infrastructure are closed.

Ten P0 items and thirteen P1 items were implemented and verified.
**Test count: 447 passing.**

| P0 item | Status |
|---|---|
| Remove production demo seeding | IMPLEMENTED · TESTED · VERIFIED |
| Secret validation at startup | IMPLEMENTED · TESTED · VERIFIED |
| `DEMO_MODE` default false + execution-mode distinction | IMPLEMENTED · TESTED · VERIFIED |
| Disable mock AI in production | IMPLEMENTED · TESTED · VERIFIED |
| Database migrations (Alembic) | IMPLEMENTED · TESTED · VERIFIED |
| Meta lead webhook ingestion | IMPLEMENTED · TESTED · VERIFIED |
| Lead scoring truthfulness | IMPLEMENTED · TESTED · VERIFIED |
| Remove demo credential prefill | IMPLEMENTED · TESTED · VERIFIED |
| Job safety (claiming, leases, states) | IMPLEMENTED · TESTED · VERIFIED |
| Authorization for financial actions | IMPLEMENTED · TESTED · VERIFIED |

Login rate limiting (original audit §7) is closed by P1-1. Rotating real secrets
remains an operational task — production refuses to boot on a placeholder or weak
secret, so a deploy that skips rotation fails loudly.

Sections 4–8 below describe the state at audit time. Where a finding has been
resolved it is annotated inline; the original text is preserved so the fix is
traceable to the finding that motivated it.

---

## 0.1 Security remediation pass — 2026-08-11

An independent security review of the post-P1 tree
([`INDEPENDENT_PRODUCTION_REVIEW.md`](INDEPENDENT_PRODUCTION_REVIEW.md)) found
one critical and four high-severity issues. All five are fixed below. **Test
count: 447 → 581**, green on SQLite **and** on PostgreSQL 16 with the
Alembic-created schema.

| # | Finding | Status |
|---|---|---|
| CRITICAL 1 | `POST /autopilot/jobs/process` drained the **global** job queue | FIXED |
| HIGH 2 | `X-Forwarded-For` trusted unconditionally | FIXED |
| HIGH 3 | Viewers could trigger expensive operations | FIXED |
| HIGH 4 | Refresh token reachable from browser JavaScript | FIXED |
| HIGH 5 | Meta `page_id` routed to the **first matching** organization | FIXED |

### CRITICAL 1 — cross-tenant job execution

`process_organization_jobs()` enqueued a job for the caller's organization and
then called `process_due()` **unscoped**, so any authenticated user could cause
another tenant's queued work — publishing, analytics sync, media generation —
to execute using that tenant's integrations and credentials.

- `JobQueue.process_due()`, `claim()`, `retry()` and `cancel()` now take an
  `organization_id`. It is applied **inside the claim `UPDATE`**, not as a
  pre-check, so a caller cannot win a race for a job it does not own.
- The worker deliberately stays unscoped: it is trusted infrastructure and has
  to drain every tenant, and a test asserts it still does.
- The endpoint now requires `campaign_publish` (admin and above) and, wherever
  a worker is deployed, **only enqueues** — returning a job id and poll URL.
  Inline execution remains a development convenience and is organization-scoped
  even there.
- Handlers verify that the record named in the payload belongs to the job's
  organization before touching it, so a forged or mistaken payload cannot make
  a worker act across tenants.

### HIGH 2 — trusted proxies

`client_ip()` returned the first `X-Forwarded-For` entry whenever the header was
present, so any client could pick its own rate-limit bucket.

- New `TRUSTED_PROXY_IPS`: unset or `none` ignores the header entirely; a list
  of IPs/CIDRs trusts it only from those peers; `*` is development-only.
- The chain is walked **from the right**, skipping our own proxies, because
  everything left of the last trusted hop is caller-supplied.
- **Production refuses to start** with the value unset, `*`, or unparseable —
  the ambiguous case is exactly the one that silently breaks the control.

### HIGH 3 — expensive operations by role

Eleven money-spending or externally-consequential endpoints were reachable with
plain authentication, including a viewer's session: AI action creation, campaign
proposal, content scheduling, creative/image/video generation and variations,
optimization analysis, the AI health narrative, integration sync, and the job
processing endpoint. All now use the existing `require_permission()` gate —
`content_write` for work a member may do, `campaign_publish` /
`integration_connect` for spend and credentials. No parallel authorization
mechanism was introduced, and no read endpoint was gated.

### HIGH 4 — refresh token storage

The API returned the refresh token in the JSON body and the frontend copied it
into `localStorage`, where any XSS payload could read it and keep a session
alive indefinitely.

- The frontend no longer stores or sends a refresh token; it relies entirely on
  the httpOnly cookie, and clears the legacy key on the way past.
- The API omits the token from the response body by default. Non-browser
  clients opt in with `X-Refresh-Token-Delivery: body`, and on `/auth/refresh`
  that opt-in is honoured **only when the request did not present the cookie** —
  otherwise injected script could simply ask for it.
- Rotation, reuse detection, revocation and expiry are unchanged and still
  tested; cookie-only session renewal is now tested end to end.

**Residual:** the access token itself is still held in `localStorage`, so XSS
can still act as the user until it expires — but it can no longer walk away
with a renewable session. Moving it to memory-only is UI work (it must survive
a page reload via a silent refresh), not a security fix, and is tracked as
P3-16.

### HIGH 5 — Meta page routing

`resolve_integration()` returned the first integration whose config mentioned
the page id, so a duplicated or stale `page_id` could file one customer's leads
into another customer's CRM.

- Ownership is resolved against **all** matching integrations. Exactly one
  match routes; zero is `unroutable`; more than one **fails closed** — the raw
  event is quarantined as `ambiguous` with no lead created and no organization
  guessed.
- Connecting a page another account already claims is rejected with 409, so the
  conflict surfaces to the person connecting rather than to a quarantined lead.

### Verification

- **581 backend tests** pass on SQLite and on PostgreSQL 16 (`alembic upgrade
  head` on a fresh container, then the full suite).
- A dedicated cross-tenant attack suite (`tests/test_tenant_isolation.py`)
  drives Organization A at Organization B's clients, leads, strategies, reports,
  creative assets, jobs, usage, billing and integrations, and asserts that a
  foreign id is indistinguishable from a missing one.
- Frontend `tsc --noEmit`, `next lint` and `next build` are green.

**One pre-existing test defect was fixed to get a green PostgreSQL run:**
`test_a_provider_outage_still_returns_the_real_scores` inserted a
`campaign_health` row referencing a random campaign id, which SQLite ignores and
PostgreSQL rejects on the foreign key.

### Verification that goes beyond "tests pass"

- **Startup guards:** 10 misconfiguration scenarios driven through the real
  FastAPI `lifespan`, not just unit-tested in isolation.
- **Migrations:** `upgrade` → `check` (no drift) → `downgrade base` → `upgrade`
  on a fresh PostgreSQL 16 container, and the full suite re-run against the
  Alembic-created PostgreSQL schema.
- **Job concurrency:** eight workers raced at one job on **real PostgreSQL**,
  because SQLite serialises writes and would not have proven the locking.
- **Credential removal:** the compiled production Next.js bundle was grepped,
  not just the source.
- **Authorization:** an automated check confirms 36 write routes are gated and
  **zero** GET routes were caught by the change.

### New risks introduced by this pass

1. **`viewer` is a new role value.** Existing rows are unaffected (`member`
   remains the default), but any external tooling that enumerates roles must be
   updated.
2. **`MEMBER` lost the ability to approve or execute actions.** This is the
   intended fix, but it is a behavioural change for existing installs — users
   who legitimately need those rights must be promoted to `ADMIN`.
3. **Meta leads can land without contact details.** When no page access token is
   stored, the lead is persisted from webhook identifiers with an explicit
   limitation and a placeholder name. This is deliberate (losing the lead would
   be worse than an incomplete one) but it needs a UI affordance and a backfill
   path once tokens exist.
4. **Pre-Alembic databases must be stamped once** (`alembic stamp head`) or the
   first migration run will fail on existing tables. See `docs/MIGRATIONS.md`.
5. **Demo data is no longer created automatically**, so a fresh developer
   environment starts empty until `./scripts/seed-demo.sh` is run.

---

## 0b. P1 infrastructure pass — 2026-08-11 (complete)

**Verdict remains NOT READY for a payment-taking launch** (Stripe/checkout is P2),
but P1 production infrastructure is closed. **447 tests passing**; frontend
typecheck, lint, and production build green.

| Plan item | Status | Evidence |
|---|---|---|
| P1-1 Distributed rate limiting | IMPLEMENTED · TESTED · VERIFIED | 17 tests, incl. two Redis instances sharing one budget |
| P1-2 S3-compatible object storage | IMPLEMENTED · TESTED · VERIFIED | 30 tests against moto (real S3 protocol) |
| P1-3 Background worker / job system | IMPLEMENTED · TESTED · VERIFIED | 20 tests incl. crash recovery + 6-worker race |
| P1-4 Structured logging | IMPLEMENTED · TESTED · VERIFIED | 32 tests incl. secret-redaction properties |
| P1-5 Global error handling | IMPLEMENTED · TESTED · VERIFIED | 37 tests incl. leak checks on every failure class |
| P1-6 Token refresh lifecycle | IMPLEMENTED · TESTED · VERIFIED | 28 tests; found a rollback that undid reuse revocation |
| P1-7 Meta lead contact backfill | IMPLEMENTED · TESTED · VERIFIED | 25 tests; no-fabrication asserted directly |
| P1-8 Usage metering | IMPLEMENTED · TESTED · VERIFIED | 24 tests; idempotency proven against retries |
| P1-9 Billing foundation | IMPLEMENTED · TESTED · VERIFIED | 43 tests; enforcement reads the P1-8 meter, no payments taken or faked |
| P1-10 Health checks | IMPLEMENTED · TESTED · VERIFIED | 21 tests; liveness proven independent of every dependency |
| P1-11 Monitoring metrics | IMPLEMENTED · TESTED · VERIFIED | Real-event counters; MonitoringAgent clarified as campaign analyst |
| P1-12 Production configuration | IMPLEMENTED · TESTED · VERIFIED | `.env.example` + fail-fast guards for every prod dependency |
| P1-13 Frontend production states | IMPLEMENTED · TESTED · VERIFIED | Async job polling; integration/campaign lifecycles surfaced |

### P1-11 — monitoring

Infrastructure metrics live in `app/observability/metrics.py` and are scraped from
`/metrics` (Prometheus) / `/metrics.json`. Counters move only when real code paths
run; unobserved series are absent, not zero. `MonitoringAgent` is intentionally
*not* infrastructure monitoring — it narrates campaign health scores that remain
arithmetic. Production refuses to boot without `METRICS_TOKEN`.

### P1-12 — production configuration

`.env.example` documents every variable production startup enforces. No demo
credentials, no mock AI, no local storage, no missing Redis/metrics token.

### P1-13 — frontend states

Creative Library polls media jobs with explicit idle/queued/generating/completed/
failed/retry UI. Integrations expose connecting and disconnected. Campaign Builder
states the approval→publish lifecycle and does not claim live publish from a build.

### P1-1 — rate limiting

Closes two audit findings at once: the unlimited login endpoint (§7, runtime-verified
at audit time as 25 failed logins all returning 401) and per-process counters that
doubled every limit under two uvicorn workers.

Redis is now the production backend and **production refuses to start without
`REDIS_URL`**, because an N-instance deployment with per-process counters hands an
attacker N times the budget. Credential endpoints are limited on the client IP and a
hashed email simultaneously, so neither rotating emails nor targeting one account
escapes the budget. Expensive generation is limited per organization, not per user.

The multi-instance claim is tested rather than asserted: two `RedisRateLimitBackend`
objects sharing one Redis server consume a single budget of 4 and the 5th request is
rejected. A Redis outage degrades to per-process counters and logs an error instead of
taking login offline; `RATE_LIMIT_DEGRADE_TO_LOCAL=false` fails closed instead.

### P1-2 — object storage

The audit found `get_object_storage()` returning `LocalObjectStorage` for every
backend value, including `s3`. A production deployment configured for S3 was writing
to an ephemeral container disk and reporting each upload as successful — the exact
"claims success without succeeding" failure this project is trying to eliminate.

`S3ObjectStorage` now implements the interface against any S3-compatible provider
(AWS, Cloudflare R2, MinIO, Wasabi — selected by `S3_ENDPOINT_URL`, not hard-coded).
`get_object_storage()` raises instead of degrading, and production refuses to start
with `STORAGE_BACKEND=local`.

Two honesty properties are enforced and tested. Every upload is followed by an
existence check, so a job cannot reach `COMPLETED` for bytes that are not retrievable
— including against a backend that accepts the write and stores nothing. And a
transport failure raises rather than returning "does not exist", so a transient outage
cannot permanently mark a healthy asset as lost; read endpoints answer 503, not 404.

Report PDFs, previously written straight to the local disk, now render to bytes and go
through the same abstraction with organization-scoped keys.

Tested against **moto**, which implements the S3 protocol, using the production boto3
client configuration rather than a hand-written stub.

### P1-3 — background worker

Media generation ran inside the HTTP request, and video polled for up to ~62 seconds
before giving up — blocking the caller and losing the job on any restart.

A worker process now runs from the same image (`python -m app.worker`) over the
existing PostgreSQL-backed queue. **No message broker was introduced**: the queue
already had atomic claiming, leases, backoff and recovery from P0-9, so adding Celery
or arq would have bought an operational dependency and no capability.

Video submission and completion are separate jobs — `media.poll_video` re-enqueues
itself with growing delay and fails with `PROVIDER_TIMEOUT` at a deadline rather than
sitting in `PROCESSING` forever. Enqueue is idempotent through a unique `dedupe_key`,
so a duplicate submit from two API instances resolves at the database rather than
generating twice.

Two properties are tested rather than assumed: six concurrent workers execute a job
exactly once, and a stop signal does not abandon an in-flight job. An end-to-end HTTP
test asserts the video endpoint returns `QUEUED` with the provider never called.

### P1-4 — structured logging

Logging was `print`-adjacent: default `logging` config, no request correlation, and
nothing linking a user-reported failure to a line in the log.

Production now emits one JSON object per line; development keeps a readable line,
because JSON in a terminal helps nobody. A request id — taken from an inbound
`X-Request-ID` when an edge proxy already set one, otherwise generated — lives in a
`ContextVar`, so every line emitted while serving that request carries it without
being threaded through call signatures. It is returned on the response, which is what
makes "send me the request id from the error" a workable support process. The worker
sets the same variable per job.

Redaction is enforced structurally rather than by convention: a logging filter scrubs
any field whose key looks like a credential (`password`, `*_token`, `*secret*`,
`api_key`, `authorization`, `cookie`, `signature`) before a formatter ever sees the
record, recursing into nested dicts and lists. Failed logins log a **hash** of the
email, not the address — a failed-login log is otherwise a ready-made list of valid
usernames for anyone who can read it.

`app/observability/events.py` gives the events an operator actually needs — auth
success and failure, authorization denial, AI and media generation, storage errors,
integration sync, webhooks, campaign execution, database errors — consistent field
names, instead of ad-hoc strings across services.

Tested as properties, not smoke tests: a secret passed as a log field does not appear
in the rendered output while neighbouring context survives; a JSON line is always a
single parseable object even when the message contains newlines; a 5 KB client-supplied
request id is truncated rather than written into every subsequent line.

### P1-5 — global error handling

Two handlers existed, for AI failures. Everything else fell through to Starlette's
default: a bare `Internal Server Error`, or FastAPI's `{"detail": …}` carrying
whatever string the raise site happened to use — including, for a database error,
the driver's message with the SQL and sometimes the row values in it.

Every failure now returns the same envelope with a stable `code`, a message written
for a human, and the `request_id`. The code is the contract the UI branches on; a
bare `503` cannot tell the frontend whether to offer a retry, whereas
`STORAGE_UNAVAILABLE` versus `CONFIGURATION_ERROR` can.

An unhandled exception returns a fixed message and nothing else. The type, the
arguments and the traceback go to the log under the same request id, so support can
find the detail from what the user quotes. Two subtler leaks were closed along the
way: SQLAlchemy errors are translated by class rather than stringified, and
validation errors return field names and messages **without pydantic's `input`
field — which would otherwise echo a rejected password back to the client** and into
any client-side error reporting.

Building the tests surfaced a real defect. An unhandled exception unwinds past the
middleware that owns the correlation `ContextVar`, so the traceback was being logged
with `request_id=None` — the one line that most needs to be findable was the one line
that could not be found. Handlers now restore the id from `request.state`, and the
error response carries the `X-Request-ID` header even on the 500 path.

The frontend `ApiError` now carries `code`, `requestId` and field errors, with
`isRetryable` and per-code copy, so a UI can distinguish "try again" from "you cannot
do this".

### P1-6 — refresh tokens

Login issued a refresh token that no endpoint accepted, so a session ended when the
access token expired an hour later. The token was also a signed JWT, which cannot be
revoked: logout removed it from the browser, and the token itself stayed valid for its
full 14 days.

Refresh tokens are now opaque random strings with a database row behind each one, and
only the SHA-256 is stored — a database dump yields no usable session. Refreshing
consumes the presented token and issues a successor in the same *family*, so each
token has exactly one legitimate use.

Presenting an already-rotated token means either theft and replay or a client
replaying, and the two cannot be told apart, so the whole family is revoked. Losing a
session is the right trade against letting a thief keep one.

**Building this found a live defect.** Rejection raises, and the request-scoped session
rolls back on an exception — so the revocation performed by reuse detection was being
undone on its way out, leaving the attacker's token working. The endpoint now commits
before raising, and a test asserts the successor is dead after a replay over HTTP.

The token is delivered as an httpOnly cookie scoped to `/api/v1/auth`, secure outside
development, so browser code need never hold it; it stays in the response body for
non-browser clients. The frontend refreshes on a 401 and retries once, which is what
actually fixes the hour-long session — concurrent 401s share one in-flight refresh,
because two parallel rotations would look exactly like reuse and revoke the session.

Every rejection returns an identical 401: distinguishing expired from revoked from
never-existed tells an attacker which token they are holding.

### P1-7 — Meta lead contact backfill

A `leadgen` webhook carries identifiers only. The name and email sit behind a Graph
API call needing a page access token that may be missing or expired when the webhook
lands. P0 already stored such leads rather than dropping them; what was missing was
any way to fill them in afterwards, so an incomplete lead stayed incomplete forever.

Retrieval can now be retried, per lead or in bulk, and every attempt is recorded with
a count, a timestamp and an `enrichment_status` of `complete`, `pending`, `failed` or
`unavailable`. A retry only ever adds information: it replaces the "unidentified"
placeholder generated at ingest, but never a value a human has since corrected, and
never an email already on the record.

The distinction that took thought is what counts as a data limitation. If the Graph
API answers successfully and returns no phone number, the form did not ask for one —
reporting that forever as missing data is noise. So after a successful retrieval only
a genuinely unusable lead is flagged; when retrieval did not happen, every empty field
is truly unknown and each is named.

A missing token is recorded on the lead rather than retried, because no amount of
backoff fixes a configuration problem, and the endpoint returns that state instead of
a hopeful "queued". Transient failures propagate so the job system's backoff applies.
Queued backfills are deduplicated per lead.

### P1-8 — usage metering

Nothing recorded what an organization consumed, so there was no basis for billing and
no way to enforce a limit.

`usage_records` stores one row per metered event rather than a running total: a
counter incremented wrongly is wrong forever with no way to find out why, whereas
rows can be inspected and a dispute settled. Idempotency is a unique column, not a
convention — every writer supplies a key derived from the event (`image:{asset_id}`),
so a retried job, a redelivered webhook and a double-click all record once. AI calls
are the exception and get a fresh key each time, because a retried job really does
call the provider again and really is charged again.

No price, rate or currency appears anywhere in this layer, and a test enforces that by
inspecting identifiers rather than prose. Prices change and differ per contract;
consumption is a fact, and mixing them means a price change silently rewrites history.

Metering AI needed the organization inside services that build their orchestrator with
no tenant argument, so the provider is wrapped and reads the organization from the
request context that auth already binds. The first version wrote each record
immediately and **doubled the test suite runtime** — a second connection opening while
the request's own transaction was still open. Usage is now buffered per request or job
and flushed once afterwards, on its own session: consumption happened whether or not
the request's transaction survived.

### P1-9 — billing foundation

The only subscription record was a plan name written at registration and read by
nothing. There was no lifecycle and no limit, so one organization could run an
unbounded provider bill against your account.

Three tables now carry it: `plans` (limits and feature flags as JSON keyed by usage
metric, so a new metered resource needs no migration), `organization_subscriptions`
(the lifecycle), and `billing_events` (append-only). The event log exists because
billing questions are almost always historical — "why was I downgraded", "when did the
trial end" — and a mutable status column cannot answer them.

`PAST_DUE` is a distinct state from `CANCELLED` and remains usable for a seven-day
grace window. A failed charge is usually an expired card, and cutting access the moment
a charge bounces loses accounts that would have paid. Trials and grace periods expire
when the subscription is read, because time passing is not an event anything listens
for.

Enforcement reads the P1-8 meter rather than keeping its own counter, so the number in
the refusal and the number on the invoice cannot drift apart. A metric absent from a
plan's limits is unlimited — a plan that forgot to mention a limit should not
accidentally block a paying customer — while an unknown *feature* defaults to denied,
because unknown means not paid for. Quota checks run as route dependencies before the
expensive work and return **402**, not 403: the caller is authenticated and permitted,
and the fix is a payment rather than a role change. The refusal is committed before the
exception propagates, or the rollback would discard the evidence behind the upgrade
prompt — the same failure mode found in P1-6.

No payment is taken and none is faked. `PaymentProvider` is an interface whose single
implementation raises on every method; a stub returning a plausible customer id would
let the system claim a subscription no provider knows about. Nothing here holds a card,
a token or a provider secret.

### P1-10 — health checks

There was a single `/health` returning a static payload, which cannot tell an
orchestrator whether the instance can serve.

Liveness and readiness are now separate because the consequences differ. A failing
liveness probe kills the container; a failing readiness probe only removes it from the
load balancer. So liveness checks nothing external — had it checked the database, a
database outage would restart every pod in a rolling loop that continues after the
database recovers, having also discarded every warm connection pool. Readiness checks
the database, the startup guards, the job table, object storage, and Redis, and returns
503 while listing every check so an operator can see which one is down.

Checks run concurrently with individual timeouts, because a probe that hangs looks
identical to a dead process. Failure details are exception types only: connection
errors carry DSNs with credentials, and the endpoint is unauthenticated. Redis is
required for readiness only in production, where the API already refuses to boot
without it; elsewhere it is reported as degraded. Documented in
`docs/HEALTH_CHECKS.md`.

---

## 1. Executive summary

**Verdict: NOT READY for production.**

GrowthOS AI is a genuinely substantial, well-architected application — roughly 130 Python modules and 43 frontend files — and it is unusually *honest* for an AI product. The team clearly took "no fake success" seriously: the media pipeline refuses to mark a job `COMPLETED` unless real bytes exist in storage, the video demo provider deliberately declines to fabricate an MP4, and publishing adapters refuse to invent an external platform ID. I verified each of these claims directly rather than trusting the docs.

Multi-tenant isolation is the strongest part of the system. I registered a hostile second organization and fired 21 cross-tenant attacks at clients, leads, strategies, creative assets, media bytes, approvals, action execution, campaign builds, and destructive writes. **Zero leaked.**

The blockers are not in the product logic — they are in the operational layer that turns an application into a SaaS. The single most dangerous finding is that `apps/api/Dockerfile` runs the demo seeder on every container start, so a production deploy would inject fake clients and fabricated analytics into the real database. Close behind: `DEMO_MODE` defaults to `true`, which silently converts publishing into simulation; the AI provider falls back to a mock that emits invented marketing analysis; there are no database migrations; there is no background worker, so a real video generation request would block an HTTP request for minutes and be lost on restart; and there is no billing, no usage metering, no error tracking, and no logging of any kind.

None of these require rewriting the architecture. They are a focused, well-bounded body of work — roughly 3–4 weeks — described in `PRODUCTION_TODO.md`.

---

## 2. Current architecture

```
┌───────────────────────────────────────────────────────────────────┐
│  Browser                                                          │
│  Next.js 15.5.7 (App Router, React 19, Tailwind)                  │
│  Token storage: localStorage  ← XSS-exposed                       │
│  (§0.1 S4: refresh token moved to an httpOnly cookie)             │
└───────────────────────┬───────────────────────────────────────────┘
                        │ fetch + Bearer JWT
┌───────────────────────▼───────────────────────────────────────────┐
│  FastAPI 0.115.6 (single uvicorn process)                         │
│  ├── /api/v1 routers (17)                                         │
│  ├── deps.get_current_auth → AuthContext(user, org, membership)   │
│  ├── services (21)  ├── ai/agents (14)  ├── integrations (8)      │
│  ├── generation (image/video providers)                           │
│  ├── automation (ExecutionEngine, safety validators)              │
│  └── in-memory rate limiter  ← per-process only                   │
└──────┬──────────────────┬──────────────────┬─────────────────────┘
       │                  │                  │
┌──────▼──────┐   ┌───────▼────────┐   ┌─────▼──────────────────────┐
│ PostgreSQL  │   │ Local disk     │   │ External providers         │
│ (or SQLite) │   │ ./storage      │   │ OpenAI · Replicate         │
│ create_all  │   │ ephemeral!     │   │ Meta · Google Ads · GA     │
│ no Alembic  │   │ no S3 adapter  │   │ YouTube                    │
└─────────────┘   └────────────────┘   └────────────────────────────┘

MISSING: Redis · worker process · object storage · CDN · error tracking
         logging · metrics · billing · CI/CD · web container
```

**Request model today:** everything is synchronous inside the API process. Image generation, video submission and polling, and campaign builds all execute inline within the HTTP request.

---

## 3. Implemented features

| # | Area | Classification | Evidence |
|---|---|---|---|
| 2 | FastAPI backend | **IMPLEMENTED** | 17 routers under `apps/api/app/api/v1/`; clean service/repository layering; consistent Pydantic schemas |
| 5 | Multi-tenancy | **IMPLEMENTED** | `app/core/deps.py:59-69`; every query filters `organization_id`. **Runtime-verified: 21/21 cross-tenant attacks blocked** |
| 9 | Image generation | **IMPLEMENTED** | `app/generation/openai_image.py` (real OpenAI Images call, `is_valid_image()` byte check at :144), `app/services/media_generation_service.py:210-299`. Runtime-verified: real PNG (`\x89PNG`, 3589 bytes) stored and served |
| 12 | Campaign builder | **IMPLEMENTED** (planning) | `app/services/campaign_build_service.py`; 9-step run record; produces plan + ad sets + ads + structured actions |
| 13 | Approval workflow | **IMPLEMENTED** | `AIActionStatus` in `app/models/enums.py`; `app/services/action_service.py`; safety re-validated on execute (`app/automation/execution.py:65-67`) — a stale approval alone is never trusted |
| 14 | Autonomy system | **IMPLEMENTED** | `app/services/autonomy_service.py`, `app/automation/safety.py`; copilot/assisted/autonomous with budget, rate, and platform caps. Runtime-verified `autonomy_mode=copilot` |
| 15 | Leads / CRM | **PARTIALLY_IMPLEMENTED** | CRUD, 7 stages, and kanban are real (`app/services/lead_service.py`, `app/api/v1/leads.py`, `clients/[id]/page.tsx:392-466`). *Gaps: no lead ingestion, no deduplication, and "AI scoring" is not AI — see §7* |

**Test suite:** 15/15 passing (`cd apps/api && PYTHONPATH=. pytest -q`), covering auth, analytics, autopilot, campaign builder, integrations, media generation, and security/mode.

---

## 4. Mock / demo features

| # | Area | Classification | Evidence & risk |
|---|---|---|---|
| 8 | AI provider abstraction | **PARTIALLY_IMPLEMENTED** | `app/ai/providers/factory.py:8-15` — **any unrecognized `AI_PROVIDER` value silently returns `MockAIProvider()`**. Nothing blocks mock in LIVE mode |
| — | Mock AI content | **MOCK** | `app/ai/providers/mock.py:24-60` returns hardcoded prose: *"rising CPL on paid social"*, *"Creative fatigue signals on top ads"*. Runtime-confirmed: strategy generation returned exactly this text as analysis of a real client |
| 16 | Analytics | **DEMO_ONLY** (default) | `app/services/analytics_service.py`; aggregation logic is real, but all data comes from `app/demo/seed.py` unless an integration is connected. Labeling is honest — `data_source` demo/live/mixed via `app/core/mode.py:16-32`, surfaced in `analytics/page.tsx:202` |
| 6 | Competitors | **DEMO_ONLY** | `app/services/competitor_service.py` — qualitative manual entry plus AI commentary; no live competitive data source |
| — | Demo image provider | **DEMO_ONLY** (honest) | `app/generation/image.py:35-70` — produces a *real* PNG labeled DEMO, and is hard-blocked when `demo_mode` is false (:46-54) |
| — | Publish simulation | **DEMO_ONLY** (honest but dangerous default) | `app/publishing/adapters.py:47-55` — in demo mode returns `success=True, status="demo_simulated"` with `external_id=None`. Correctly labeled, **but `DEMO_MODE` defaults to `true`** (`app/core/config.py:15`) |
| — | "AI lead scoring" | ~~**MOCK label / real rule engine**~~ → **RESOLVED (P0-7)** | `app/ai/orchestrator.py:55-56` — `return self.lead_agent.deterministic_score(request)` **always bypasses the LLM**. The scorer itself is honest arithmetic (`app/ai/agents/lead_agent.py:45-80`, `score = 35` plus increments), but the UI presents it as AI scoring. **This was the one place the product's naming overstated what runs.** Renamed to deterministic scoring throughout, and the output now carries `method`, `evidence[]` and `data_limitations[]`. An LLM was deliberately *not* introduced: the system tracks no behavioural signals, so a model could only speculate about them |
| — | Meta lead webhook | **NOT_IMPLEMENTED (silent success)** | `app/api/v1/webhooks.py:35-36` verifies the signature then returns `{"received": True}` **without creating a lead or persisting anything**. Meta will consider delivery successful; the lead is dropped |
| — | Seeded lead scores | **DEMO_ONLY** | `app/demo/seed.py:277-288` — `score = 55 + idx * 7` with written-in explanations |

---

## 5. Broken features

| # | Area | Classification | Evidence |
|---|---|---|---|
| 25 | Production deployment | **BROKEN** | `apps/api/Dockerfile:19` — `CMD ["sh","-c","python -m app.demo.seed && uvicorn ..."]`. **Every container start seeds demo organizations, clients and fabricated analytics into the target database.** Deploying this to production corrupts real data |
| 4 | Token refresh | **BROKEN** | `app/services/auth_service.py:57,71` issues a `refresh_token`, but **no `/auth/refresh` endpoint exists** (`app/api/v1/auth.py` has only register/login/me/mode). Frontend stores it (`apps/web/src/lib/api.ts:16-19`) and never uses it. Users are hard-logged-out after 60 minutes |
| 11 | Background jobs / workers | **NOT_IMPLEMENTED** | `app/jobs/queue.py` is a DB-backed queue whose `process_due()` is only reachable from `app/jobs/handlers.py:70`. **No worker entrypoint, no scheduler, no Redis/Celery in `requirements.txt`.** `MediaGenerationService.enqueue_video` (`:154`) polls inline for up to ~62s inside the HTTP request; a restart loses the job permanently |
| 3 | Database migrations | **BROKEN** | `apps/api/alembic/versions/` is **empty**; `app/main.py:19` runs `Base.metadata.create_all` at startup. No versioned schema, no rollback. `app/db/schema_migrate.py` performs ad-hoc SQLite column patching |

---

## 6. Missing features

| # | Area | Classification | Notes |
|---|---|---|---|
| 23 | Billing / subscriptions | **NOT_IMPLEMENTED** | Zero Stripe references repo-wide. `Subscription` model (`app/models/ai_ops.py:73-80`) is an inert row created at registration with `plan="starter"`; nothing reads or enforces it |
| 24 | Usage tracking | **NOT_IMPLEMENTED** | No token counting, no AI cost attribution, no per-org quotas, no credit ledger. An organization can generate unlimited paid OpenAI/Replicate media at your expense |
| 22 | Error handling | **NOT_IMPLEMENTED** | No global exception handler, **no `logging` import anywhere in `apps/api/app`**, no request IDs, no Sentry. A 500 produces an uncorrelatable stack trace on stdout |
| 10 | Video generation (live) | **PARTIALLY_IMPLEMENTED** | `app/generation/replicate_video.py` is a real, complete adapter with `is_valid_video()` MP4 verification (:239). **Never executed against live Replicate** — no credentials configured. Unverifiable until keys exist, and unusable at scale without a worker |
| 18 | Integrations (write path) | **NOT_IMPLEMENTED** | OAuth + read sync are real (`google_ads.py`, `google_analytics.py`, `meta_family.py`, `youtube.py`). **All live writes refuse honestly**: `app/publishing/adapters.py:57-62` returns `PUBLISH_NOT_AVAILABLE`; `app/automation/execution.py:407-410` returns `LIVE ADS WRITE NOT AVAILABLE` |
| 7 | Object storage | **PARTIALLY_IMPLEMENTED** | Only `LocalObjectStorage` exists; `get_object_storage()` (`app/storage/object_storage.py:95-101`) falls back to local for *any* backend value including `s3`. Container disk is ephemeral and **`docker-compose.yml` mounts no volume for the API** — all generated media is lost on container recreate. No signed URLs (everything proxies through the API) and no max file size anywhere in the pipeline |
| 17 | Reports | **PARTIALLY_IMPLEMENTED** | Real reportlab PDF (`app/services/report_service.py:223`), but written to `Path(settings.storage_local_path)/"reports"` (:205), bypassing the storage abstraction — also lost on redeploy |
| 1 | Frontend | **PARTIALLY_IMPLEMENTED** | All 21 pages are real (`PhasePlaceholder.tsx` is defined but never used — dead code). No error boundaries, no refresh handling, tokens in localStorage. Content Studio / Leads / AI Assistant are thin client-pickers that redirect into the client workspace |
| 21 | Audit logging | **PARTIALLY_IMPLEMENTED** | `app/security/audit.py` + `AuditLog` model are real and written on auth, clients, actions, integrations, reports, and strategy. **But `ip_address` is never populated, media generation and campaign builds are not logged, there is no read API, and the table is not tamper-evident** |
| — | Assistant conversation history | **PARTIALLY_IMPLEMENTED** | `AIConversation` rows are written (`app/api/v1/assistant.py:47-59`) but **no endpoint reads them back**, and each row stores a single turn — the assistant has no memory across messages |
| — | Monitoring agent | **NOT_IMPLEMENTED (dead code)** | `app/ai/agents/monitoring_agent.py` is fully written but `.monitor(` is never called outside the orchestrator definition |

---

## 7. Security risks

Verified findings, ordered by severity.

| Severity | Finding | Evidence |
|---|---|---|
| **P0** | **Placeholder secrets accepted silently.** `secret_key="dev-secret-change-me"`, `encryption_key="change-me-32-byte-fernet-compatible-key!!"`. No validation at boot — the app runs happily with a publicly-known JWT signing key, allowing full token forgery | `app/core/config.py:21,26` |
| **P0** | **Demo seeder runs on container start**, writing fake data to whatever database is configured | `apps/api/Dockerfile:19` |
| **P0** | **`DEMO_MODE` defaults to `true`.** A deploy that forgets the variable silently simulates publishing while reporting success | `app/core/config.py:15` |
| **P0** | **Mock AI usable in LIVE mode.** Fabricated marketing analysis presented to paying customers as real insight | `app/ai/providers/factory.py:15` |
| **P1** | **Login endpoint is not rate limited.** Runtime-verified: 25 consecutive failed logins all returned 401, never 429. Unlimited credential brute-force | `rate_limit_dependency` is attached to `get_current_auth` (`app/core/deps.py:43`) only; `/auth/login` has no dependency |
| **P1** | **Rate limiting is per-process in-memory.** Two uvicorn workers = 2× the limit; a restart clears all counters | `app/security/rate_limit.py:18` |
| **P1** | **No role-based authorization.** `MemberRole` exists but is enforced in exactly one place (org demo-mode toggle). Any invited `member` can delete clients, approve financial actions, and execute campaigns | `app/services/auth_service.py:110` is the only check; no `require_role` dependency exists |
| **P1** | **JWT cannot be revoked.** No `jti`, no denylist, no logout endpoint. A stolen token is valid for its full 60 minutes | `app/core/security.py:21-29` |
| **P1** | **Tokens in `localStorage`** — readable by any XSS payload | `apps/web/src/lib/api.ts:13,17` — *partially closed by §0.1 S4: the refresh token is now an httpOnly cookie and never touches web storage; the access token (60-minute default) still lives in `localStorage`* |
| **P1** | **No security headers** — runtime-verified absent: HSTS, X-Frame-Options, X-Content-Type-Options, CSP | `app/main.py:28-34` adds only CORS |
| **P1** | **`/webhooks/meta` is unauthenticated and unrate-limited**, and silently returns success without processing. An open, unmetered endpoint that also loses real leads | `app/api/v1/webhooks.py:35-36` |
| **P1** | **Demo credentials pre-filled on the login form.** Ships a working account hint to every visitor | `apps/web/src/app/(auth)/login/page.tsx:13-14` |
| **P2** | **Job rows can strand in `running`.** Status is set before the handler executes with no lease or recovery, and `process_due()` has no `SELECT ... FOR UPDATE` — concurrent workers could double-process | `app/jobs/queue.py:64-67` |
| **P2** | CORS is correctly restrictive by default, but `allow_credentials=True` with `allow_methods=["*"]`/`allow_headers=["*"]` becomes dangerous if anyone sets `API_CORS_ORIGINS=*` | `app/main.py:28-34` |
| **P2** | No account lockout, no password complexity beyond `min_length=8`, no email verification, no password reset | `app/schemas/auth.py:8` |

**Verified clean — no action needed:**

- **No SQL injection.** All access is through SQLAlchemy ORM; no raw `text()` or f-string SQL in query paths.
- **No XSS sinks.** Zero `dangerouslySetInnerHTML` in `apps/web/src`.
- **No committed secrets.** `git ls-files` shows only `.env.example`; `.gitignore` covers `.env`, `apps/api/.env`, `apps/web/.env.local`.
- **Path traversal handled.** `app/storage/object_storage.py:41-47` resolves and boundary-checks every key.
- **OAuth tokens encrypted at rest** with Fernet and never returned to the browser (`app/security/secrets.py`).
- **Media bytes require auth** and are org-scoped (`app/api/v1/creative.py:181-203`).

---

## 8. Multi-tenancy risks

**Classification: IMPLEMENTED — runtime-verified.**

I created a hostile organization ("Evil Corp B") via `/auth/register` and attempted 21 attacks against the demo organization's live resource IDs:

| Attack surface | Result |
|---|---|
| `GET /clients/{A}` | `404 Client not found` |
| `GET /clients/{A}/leads`, `/strategies`, `/content/posts`, `/content/calendar` | `200 []` (scoped empty) |
| `POST /clients/{A}/assistant/chat` | `404` |
| `GET /creative/assets`, `/autopilot/creative/library` | `200 []` |
| `GET /creative/media/{assetA}` (raw bytes) | `404` |
| `POST /creative/{assetA}/variations` | `404 ASSET_NOT_FOUND` |
| `POST /creative/images/generate` / `videos/generate` on client A | `404` |
| `POST /autopilot/campaigns/build` on client A | `404` |
| `POST /clients/{A}/strategies/generate` | `404` |
| `POST /autopilot/actions/{A}/approve` · `/execute` · `/reject` | `404 Action not found` |
| `PATCH` / `DELETE /clients/{A}`, `PATCH` lead A | `404` |
| Unauthenticated access to all of the above | `401` |

**Leaks: NONE.** Coverage confirmed across clients, leads, campaigns, analytics, assets, reports, strategies, conversations, integrations, and notifications.

**Residual risks (design, not leakage):**

1. **One organization per user, silently.** `app/core/deps.py:59-64` and `auth_service.me()` both select membership with `.limit(1)` and no ordering. A user belonging to two organizations is bound to an arbitrary one, with no way to switch. This blocks the agency multi-workspace use case and is non-deterministic.
2. **No organization-scoped DB enforcement.** Isolation depends entirely on every future query remembering its `organization_id` filter. Postgres Row-Level Security would make this structural rather than a code-review discipline.
3. **Storage keys are correctly namespaced** (`organizations/{org}/clients/{client}/...`), which will map cleanly onto S3 prefixes.

---

## 9. Image-generation findings

**Classification: IMPLEMENTED (real). Verified end to end.**

Traced the full chain:

```
creative-library/page.tsx
  → POST /api/v1/creative/images/generate        app/api/v1/creative.py:71
  → ClientService.get_client()                   tenant check
  → MediaGenerationService.enqueue_images()      media_generation_service.py:32
  → ImageJob row, status=QUEUED                  :69-78
  → provider.generate_image()                    openai_image.py / image.py
  → is_valid_image(bytes)                        media_generation_service.py:235
  → storage.upload(key)                          :252
  → storage.exists(key)  ← re-verified           :253
  → CreativeAsset row + storage_key              :262-283
  → status=COMPLETED                             :286
  → GET /api/v1/creative/media/{asset_id}        creative.py:181 (authenticated)
  → MediaPreview.tsx blob URL                    authenticated fetch
```

**Runtime evidence:** generated asset `c3d4ec48…`, status `COMPLETED`, endpoint returned **3589 bytes with magic number `\x89PNG\r\n\x1a\n`** and `Content-Type: image/png`. Creative Library listed 125 assets.

**No mock URLs, no placeholder thumbnails, no fake asset records.** Three independent guards prevent false success:

1. `is_valid_image()` rejects non-image bytes (`:235`).
2. `storage.exists()` is re-checked after upload; failure sets `STORAGE_UPLOAD_FAILED` (`:253-259`).
3. `_image_job_payload()` **self-heals a lie** — if a job is `COMPLETED` but the file is missing, it rewrites the status to `FAILED` with `COMPLETED_WITHOUT_FILE` (`:438-442`).

**Gaps:** runs inline in the request (a slow DALL·E call blocks a worker); no per-org spend cap; stored on ephemeral local disk.

---

## 10. Video-generation findings

**Classification: PARTIALLY_IMPLEMENTED. Adapter is real; live path unverified; no worker.**

```
creative-library → POST /creative/videos/generate    creative.py:103
  → MediaGenerationService.enqueue_video()           :109
  → VideoJob QUEUED → SUBMITTED                      :312
  → ReplicateVideoProvider.generate_video()          replicate_video.py:24
  → provider job id captured                         :110-121
  → INLINE bounded poll, 6 attempts, ~62s max        media_generation_service.py:328-341
  → _materialize(): download → is_valid_video()      replicate_video.py:239
  → storage.upload → exists() check                  :376-383
  → CreativeAsset + COMPLETED                        :386-421
```

**The simulation question — answered:** video generation is **not** simulated, and I identified the code that deliberately refuses to simulate it:

```python
# app/generation/video.py:74-84  (DemoVideoProvider)
return GenerationResult(
    success=False,
    status="failed",
    message="DEMO — no playable video file generated. Configure VIDEO_PROVIDER=replicate ...",
    error="DEMO_VIDEO_FILE_NOT_GENERATED",
)
```

Runtime-confirmed: the API returned `status=FAILED`, `error="VIDEO GENERATION NOT CONFIGURED"`. **No fake MP4, no fake COMPLETED, no script-passed-off-as-video.** This is correct behavior.

**Blockers for production video:**

1. **No worker.** Real generation takes 1–10 minutes; the inline poll caps at ~62 seconds, then leaves the job `PROCESSING` and relies on the user re-polling `GET /creative/videos/jobs/{id}`. An API restart orphans the job forever.
2. **No webhook.** Replicate supports completion webhooks; only polling is implemented.
3. **Never executed live** — no `VIDEO_API_KEY`/`VIDEO_MODEL` configured, so the adapter is unproven against the real API.

---

## 11. Campaign-execution findings

**Classification: draft/plan IMPLEMENTED; live execution NOT_IMPLEMENTED (honestly refused).**

```
AI strategy      ✅  strategy_service.py            (mock AI by default)
campaign plan    ✅  campaign_build_service.py      real plan, ad sets, ads
creative gen     ✅  images real / videos gated
approval         ✅  AIAction PENDING→APPROVED, re-validated on execute
integration      ⚠️  OAuth + read sync real; write scopes absent
external API     ❌  NOT_IMPLEMENTED
external ID      ❌  never produced
database         ✅  AutopilotRun + AIAction + ActionExecution persisted
analytics        ⚠️  demo rows unless an integration is synced
```

**Critically, the application does not fake this.** Per your rule — *"if the application says a campaign was published without receiving a real external provider response, classify as BROKEN"* — I checked every path that could lie:

```python
# app/automation/execution.py:365-372  (_exec_publish)
if not pub.success or not pub.external_id:
    return {"confirmed": False, "error": pub.error or pub.message, ...}
```

```python
# app/automation/execution.py:407-410  (_exec_ads_mutation)
return {"confirmed": False,
        "error": "LIVE ADS WRITE NOT AVAILABLE — connect scopes and enable write adapters."}
```

An action can only reach `COMPLETED` when `confirmed` or `demo` is true (`:83-86`), and a real (non-demo) publish requires a genuine `external_id`. **This is correct and is not BROKEN.**

**The one real danger** is configuration, not code: because `DEMO_MODE` defaults to `true`, a production deploy that omits the variable would return `success=True / "demo_simulated"` for publishes. The label is honest, but an operator scanning for `success` would be misled. Fixing the default and adding a strict-live guard resolves it.

---

## 12. Required environment variables

Fully documented in the rewritten **[`.env.example`](.env.example)**, which now separates `[dev]` / `[staging]` / `[prod]` requirements per variable. Summary of what production additionally demands:

| Variable | Why it is required in production |
|---|---|
| `ENVIRONMENT=production` | Enables startup safety checks, disables debug output |
| `DEMO_MODE=false` | Prevents simulated publishing being reported as success |
| `STRICT_LIVE_MODE=true` | Hard-blocks mock AI and demo media providers |
| `SECRET_KEY` | Real 32-byte random value; the default allows JWT forgery |
| `ENCRYPTION_KEY` | Real Fernet key protecting stored OAuth tokens |
| `DATABASE_URL` / `DATABASE_URL_SYNC` | Managed Postgres with TLS |
| `DB_AUTO_CREATE=false` | Forces Alembic migrations instead of `create_all` |
| `REDIS_URL` | Durable job queue + shared rate limiting |
| `INLINE_JOB_EXECUTION=false` | Moves media generation to the worker |
| `AI_PROVIDER` + key | `mock` fabricates analysis and must not run in production |
| `IMAGE_PROVIDER=openai` + key | Real creative generation |
| `VIDEO_PROVIDER=replicate` + key + model | Real playable video |
| `STORAGE_BACKEND=s3` + S3 credentials | Media survives redeploys |
| `AUTH_RATE_LIMIT_PER_MINUTE` | Brute-force protection on login |
| `SENTRY_DSN`, `LOG_FORMAT=json` | Operability |
| `DEBUG_ERRORS=false` | Prevents internal detail leaking to clients |

---

## 13. Required third-party accounts

| Service | Purpose | Necessity | Approx. cost |
|---|---|---|---|
| **Managed Postgres** (Neon / Supabase / RDS) | Primary datastore + backups | **Required** | $20–70/mo |
| **Redis** (Upstash / ElastiCache) | Queue + rate limiting | **Required** | $10–30/mo |
| **S3 or Cloudflare R2** | Media + report storage | **Required** | $5–25/mo |
| **OpenAI** | Text AI + DALL·E images | **Required** | usage-based |
| **Replicate** | Video generation | Required *for video* | usage-based |
| **Sentry** | Error tracking | **Required** | free–$26/mo |
| **Container host** (Railway / Render / Fly / ECS) | API + worker | **Required** | $20–100/mo |
| **Vercel** (or equivalent) | Next.js frontend | **Required** | free–$20/mo |
| **Domain + DNS** (Cloudflare) | `app.` / `api.` + TLS | **Required** | ~$15/yr |
| **Stripe** | Billing | Required to charge money | 2.9% + 30¢ |
| **Meta App** (Business verified) | Meta/Instagram/WhatsApp | Per-integration | free (review req.) |
| **Google Cloud + Ads developer token** | GA / Google Ads / YouTube | Per-integration | free (approval req.) |
| **Transactional email** (Resend / SES) | Verification, resets, alerts | **Required** | free–$20/mo |

> Meta App Review and Google Ads developer token approval each take **2–6 weeks**. Start these immediately — they are the long pole for live campaign execution.

---

## 14. Required production infrastructure

Deliberately minimal — no Kubernetes, no service mesh, no microservices.

```
                    Cloudflare (DNS · TLS · WAF)
                              │
              ┌───────────────┴───────────────┐
              ▼                               ▼
      app.yourdomain.com              api.yourdomain.com
      Vercel — Next.js                Container host
                                      ┌──────────────────┐
                                      │ API  (2 × uvicorn)│
                                      │ Worker (1 × )     │  ← same image
                                      └────────┬─────────┘
                     ┌─────────────────────────┼──────────────────────┐
                     ▼                         ▼                      ▼
              Managed Postgres            Redis                  S3 / R2
              (daily backups, PITR)    (queue + limits)      (media + reports)
                                             │
                                             ▼
                                   Sentry · uptime monitor
```

**Minimum viable footprint:** 2 API instances (rolling deploys), 1 worker, 1 Postgres, 1 Redis, 1 bucket. Roughly **$80–150/month** before AI usage.

**Explicitly not needed yet:** Kubernetes, Kafka, Elasticsearch, a data warehouse, read replicas, or a separate analytics service.

---

## 15. Deployment plan

**Phase A — Make it safe to deploy (week 1)**
Remove the seeder from the Dockerfile; add a startup guard that refuses to boot in production with placeholder secrets or `AI_PROVIDER=mock`; flip `DEMO_MODE` to default `false`; generate the initial Alembic migration and set `DB_AUTO_CREATE=false`.

**Phase B — Make it survive (week 2)**
Add S3/R2 storage, Redis, and a worker process; move media generation and report PDFs off the request path and off local disk; add a Replicate webhook.

**Phase C — Make it operable (week 2–3)**
Structured JSON logging with request IDs, a global exception handler that never leaks internals, Sentry, `/health` plus a readiness probe, security headers, login rate limiting, refresh + logout endpoints, and role-based authorization.

**Phase D — Make it a business (week 3–4)**
Stripe subscriptions and webhooks, per-organization usage metering and quota enforcement, plan gating, and an audit-log viewer.

**Phase E — Go live (week 4)**
Deploy to staging with production-shaped config; run the full `GrowthOS_AI_Complete_Testing_Guide` against staging; complete Meta/Google app review; connect one real ad account in `COPILOT` mode with a small budget; verify a real external campaign ID is stored; then open to users.

**Rollout order:** database → API → worker → frontend. Keep the previous image one command away for rollback.

---

## 16. Pre-launch checklist

**Blocking (must all be true)**

- [ ] Demo seeder removed from the production container entrypoint
- [ ] `DEMO_MODE=false` and `STRICT_LIVE_MODE=true` verified in the production environment
- [ ] Startup refuses to boot with placeholder `SECRET_KEY` / `ENCRYPTION_KEY`
- [ ] `AI_PROVIDER` is a real provider; mock is impossible in production
- [ ] Alembic migration generated, applied, and rollback tested
- [ ] `DB_AUTO_CREATE=false`; automated backups + point-in-time recovery verified by an actual restore
- [ ] Media stored in S3/R2 and still retrievable after a redeploy
- [ ] Worker running; a video job survives an API restart
- [ ] Login rate limited (verify a 429)
- [ ] Refresh + logout implemented; token revocation works
- [ ] Role-based authorization enforced on destructive and financial endpoints
- [ ] Security headers present (HSTS, CSP, X-Frame-Options, X-Content-Type-Options)
- [ ] HTTPS everywhere; HTTP redirects to HTTPS
- [ ] `API_CORS_ORIGINS` set to exact production origins, never `*`
- [ ] Sentry receiving events; structured logs with request IDs
- [ ] Cross-tenant isolation re-verified against staging
- [ ] Real image generated in production config and retrievable
- [ ] Real video generated end-to-end, or the feature is explicitly disabled in the UI
- [ ] A live campaign publish either returns a real external ID or fails honestly
- [ ] Per-organization AI spend cap enforced

**Non-blocking but strongly recommended**

- [ ] Stripe live and gating plan limits
- [ ] Email verification and password reset
- [ ] Terms of Service, Privacy Policy, DPA, GDPR deletion path
- [ ] CI running tests on every push
- [ ] Load test at expected peak concurrency
- [ ] Audit-log viewer for customer disputes

---

## 17. Post-launch monitoring plan

**Alert immediately (page someone)**

| Condition | Why |
|---|---|
| API 5xx rate > 1% over 5 min | Service degradation |
| p95 latency > 2s over 10 min | Capacity or a blocking call |
| Worker queue depth > 100, or oldest job > 15 min | Worker stalled |
| Any `COMPLETED_WITHOUT_FILE` occurrence | The integrity guard fired — storage pipeline broken |
| Any action `COMPLETED` in live mode without an `external_id` | Potential fake-success regression |
| Postgres connections > 80% of pool | Exhaustion imminent |
| Failed logins > 50/min from one IP | Credential stuffing |
| AI spend > 150% of daily baseline | Runaway cost or abuse |

**Dashboard (review daily)**
Request volume and error rate per endpoint; media job outcomes by provider; AI cost per organization; signup and activation funnel; integration sync failures; approval queue age.

**Weekly review**
Slowest endpoints, most common failure codes, per-tenant usage outliers, storage growth, and a sample audit-log read to confirm actions are attributable.

**Product-integrity checks (unique to this system, and the reason the app is trustworthy — keep them monitored)**
Alert on any live-mode asset with `data_source="demo"`; any campaign marked published without a platform response; any analytics KPI whose `data_source` is `mixed` in a live organization.

---

## 18. Exact recommended order of fixes

Strictly sequential — each step unblocks the next.

1. **Remove `python -m app.demo.seed` from `apps/api/Dockerfile`.** One line; prevents production data corruption. Do this first.
2. **Add a production startup guard** rejecting placeholder secrets, `DEMO_MODE=true`, and `AI_PROVIDER=mock` when `ENVIRONMENT=production`.
3. **Change `demo_mode` default to `false`** in `app/core/config.py` and make demo opt-in.
4. **Generate the initial Alembic migration**, set `DB_AUTO_CREATE=false`, remove `create_all` from the production path.
5. **Implement S3/R2 storage** behind the existing `ObjectStorage` interface, and route report PDFs through it.
6. **Add Redis + a worker process**; move image/video generation off the request path; add the Replicate webhook.
7. **Add logging, a global exception handler, request IDs, and Sentry.**
8. **Fix authentication:** `/auth/refresh`, `/auth/logout`, `jti` + revocation denylist, and login rate limiting.
9. **Add role-based authorization** (`require_role`) on destructive, financial, and integration endpoints.
10. **Add security headers** and enforce HTTPS.
11. **Add per-organization usage metering and AI spend caps** — protects your margin before you have customers.
12. **Add Stripe billing** and plan gating.
13. **Resolve the multi-organization membership ambiguity** (explicit active-org selection).
14. **Add CI** running the existing 15 tests plus a cross-tenant isolation test on every push.
15. **Complete Meta App Review and the Google Ads developer token**, then implement live write adapters — the only remaining path to genuine campaign execution.
16. **Optional hardening:** Postgres Row-Level Security, so tenant isolation is enforced by the database rather than by code discipline.

---

*Prepared by: Lead Production Engineer. Every "IMPLEMENTED" classification in this document was verified by reading the implementation and, where a runtime claim was made, by executing it against the running application.*
