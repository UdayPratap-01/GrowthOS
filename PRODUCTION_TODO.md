# GrowthOS AI — Production TODO

Companion to [`PRODUCTION_READINESS.md`](PRODUCTION_READINESS.md). Every task below traces to a verified finding in that audit.

**Priorities**

| | Meaning |
|---|---|
| **P0** | Critical security or data-integrity risk. Fix before *any* deploy, including staging with real data. |
| **P1** | Production blocker. The system cannot reliably serve paying customers without it. |
| **P2** | Launch requirement. Needed to charge money and operate the business. |
| **P3** | Post-launch improvement. |

Effort: **S** ≈ <½ day · **M** ≈ 1–2 days · **L** ≈ 3–5 days

---

## P0 — Critical security / data risks

> **Status — hardening pass completed 2026-08-10.** 8 of the 10 items below are
> IMPLEMENTED + TESTED + VERIFIED. Two remain open: **P0-5 (login rate limiting)**
> and **P0-10 (rotate real secrets)** — neither was in the scope of the hardening
> request, and P0-10 is an operational task that cannot be completed in code.
>
> Two additional P0-class items were delivered in the same pass and are tracked
> at the end of this section as **P0-11 (job safety)** and **P0-12 (role-based
> authorization)**.
>
> | Item | Status | Verification |
> |---|---|---|
> | P0-1 demo seeding | **VERIFIED** | seeder exits 2 under `ENVIRONMENT=production`; Dockerfile CMD asserted seed-free |
> | P0-2 startup guard | **VERIFIED** | 10 boot scenarios exercised against the real `lifespan` |
> | P0-3 `DEMO_MODE` default | **VERIFIED** | default is `False`; production boot fails on `true` |
> | P0-4 mock AI | **VERIFIED** | unknown provider raises; mock blocked in production |
> | P0-5 login rate limit | **OPEN** | not in scope of this pass |
> | P0-6 Meta webhook | **VERIFIED** | 9 tests: persistence, idempotency, signature, malformed, unroutable, DB failure |
> | P0-7 lead scoring label | **VERIFIED** | renamed to deterministic; 10 tests incl. anti-fabrication guard |
> | P0-8 demo credentials | **VERIFIED** | absent from the production bundle (grep of `.next/`) |
> | P0-9 Alembic migration | **VERIFIED** | `upgrade`/`check`/`downgrade` round-trip on fresh Postgres; full suite green on PG |
> | P0-10 rotate secrets | **OPEN** | operational; startup guard now *enforces* it |
> | P0-11 job safety | **VERIFIED** | 18 tests incl. 8-worker race, on SQLite **and** PostgreSQL |
> | P0-12 authorization | **VERIFIED** | 41 tests; 36 write routes gated; 0 GET routes affected |

### P0-1 · Remove the demo seeder from the container entrypoint — **S** — ✅ IMPLEMENTED · TESTED · VERIFIED
`apps/api/Dockerfile:19` runs `python -m app.demo.seed` on **every** container start, which would write demo organizations, fake clients, and fabricated analytics into the production database.

```dockerfile
# Replace:
CMD ["sh", "-c", "python -m app.demo.seed && uvicorn app.main:app --host 0.0.0.0 --port 8000"]
# With:
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
```
Keep seeding as an explicit local-only command in `scripts/`.
**Done when:** a fresh container against an empty database creates zero rows.

**Resolved.** `apps/api/Dockerfile` now runs uvicorn only. `app/demo/seed.py`
gained `assert_seeding_allowed()`, which raises `SeedBlockedError` when
`ENVIRONMENT=production` or `ALLOW_DEMO_SEED=false`; `__main__` exits 2.
Seeding is now the explicit `./scripts/seed-demo.sh`.
**Verified:** `ENVIRONMENT=production python -m app.demo.seed` → exit 2 with a
precise refusal. `tests/test_production_guards.py` (4 tests) also asserts the
Dockerfile `CMD` contains no seed step, so the regression cannot silently return.

---

### P0-2 · Add a production startup guard — **M** — ✅ IMPLEMENTED · TESTED · VERIFIED
Nothing currently stops the app booting with a publicly-known JWT key. Add a `validate_production_config()` called from the `lifespan` in `app/main.py` that raises when `ENVIRONMENT=production` and any of the following hold:

- `SECRET_KEY` or `ENCRYPTION_KEY` still contains `change-me` / `CHANGE_ME` / `dev-secret`
- `DEMO_MODE=true`
- `AI_PROVIDER=mock`
- `STORAGE_BACKEND=local`
- `DATABASE_URL` points at SQLite
- `API_CORS_ORIGINS` contains `*`

**Done when:** a deliberately misconfigured production boot fails loudly with a precise message instead of starting.

**Resolved.** `app/core/startup_checks.py:validate_configuration()` is called
first in the `app/main.py` lifespan. It rejects missing, placeholder and
low-entropy secrets (≥32 chars and ≥8 distinct characters, matched against a
placeholder list), and in production also rejects `DEMO_MODE=true`, mock/unknown
`AI_PROVIDER`, a provider without its API key, SQLite, wildcard CORS and
`DB_AUTO_CREATE=true`. All errors are collected and reported together.

`STORAGE_BACKEND=local` is deliberately **not** enforced: the S3/R2 adapter is
P1-1 and does not exist yet, so enforcing it would make production unbootable.
Re-add that check as part of P1-1.

**Verified:** 10 boot scenarios run against the real lifespan — missing,
placeholder and weak `SECRET_KEY`, `DEMO_MODE=true`, `AI_PROVIDER=mock`, SQLite,
wildcard CORS and `DB_AUTO_CREATE=true` all refuse to start; a strong production
config and a development default config both start.

---

### P0-3 · Flip `DEMO_MODE` to default `false` — **S** — ✅ IMPLEMENTED · TESTED · VERIFIED
`app/core/config.py:15` defaults to `True`. A deploy that forgets the variable silently turns publishing into simulation (`app/publishing/adapters.py:47-55`) while returning `success=True`. Demo must be opt-in, never the fallback.
**Done when:** an environment with no `DEMO_MODE` set operates in LIVE mode.

**Resolved.** `demo_mode` now defaults to `False` and production refuses to boot
with it enabled. The three execution modes are now explicit rather than implied
by a boolean — `app/core/mode.py:ExecutionMode` defines `DEMO_DATA`,
`DEMO_EXECUTION` and `REAL_EXECUTION`, and `PublishResult` carries an
`execution_mode` field plus an `is_real_publish` property that is true only when
a platform returned an external ID. Demo publishes are labelled
`DEMO EXECUTION … no live platform post was created`.

The UI already surfaced this honestly (`components/layout/Topbar.tsx` renders a
persistent DEMO/LIVE banner and an "Env DEMO_MODE still on" warning badge), so
no frontend change was required.

---

### P0-4 · Prevent the mock AI provider from ever running in production — **S** — ✅ IMPLEMENTED · TESTED · VERIFIED
`app/ai/providers/factory.py:8-15` returns `MockAIProvider()` for *any* unrecognized value. The mock emits invented analysis ("rising CPL on paid social") that would be presented to customers as real insight.

Raise on unknown providers instead of falling back, and block `mock` when `STRICT_LIVE_MODE=true`.
**Done when:** `AI_PROVIDER=typo` fails fast rather than silently fabricating content.

**Resolved.** The factory has no fallback branch at all. Unknown names raise
`AIProviderConfigurationError`; `mock` is permitted only outside production; a
real provider without its API key raises rather than degrading to mock.

Failure states are now distinct at the API boundary:

| Situation | Response |
|---|---|
| Real provider succeeds | normal 2xx |
| Real provider call fails | `502 AI_GENERATION_FAILED` |
| No / misconfigured provider | `503 CONFIGURATION_ERROR` |
| Mock provider in production | startup refuses to boot |

`AIGenerationError` was added to `app/ai/providers/base.py`, and both the OpenAI
and Anthropic adapters wrap transport errors, unreadable responses and schema
mismatches in it, so a failed call surfaces as a failure instead of falling
through to invented content.

---

### P0-5 · Rate limit the login endpoint — **S** — ✅ CLOSED BY P1-1
**Original finding:** 25 consecutive failed logins returned 401 every time, never 429. Unlimited credential brute-force. `rate_limit_dependency` was only wired into `get_current_auth` (`app/core/deps.py:43`), which unauthenticated routes never touch.

Resolved by the P1-1 rate-limiting work below: `/auth/login` and `/auth/register` now carry `auth_rate_limit`, keyed on IP **and** hashed email.
**Verified:** `test_repeated_failed_logins_eventually_return_429` asserts the pattern `401 ×5 → 429 ×3`.

---

### P0-6 · Stop the Meta webhook silently discarding real leads — **M** — ✅ IMPLEMENTED · TESTED · VERIFIED
`app/api/v1/webhooks.py:35-36` verifies the signature and then returns `{"received": True}` **without creating a lead or storing the payload**. Meta records a successful delivery, so the lead is lost with no error anywhere. The endpoint is also unauthenticated and unrate-limited.

Either persist the raw payload and create `Lead` rows, or return a non-success status so Meta retries. **Do not acknowledge a webhook you did not process.** Add rate limiting and a payload size cap while you are in there.
**Done when:** a simulated Meta lead payload produces a `Lead` row, and an unprocessable payload is not acknowledged as received.

**Resolved.** `app/services/lead_ingest_service.py` parses the payload, resolves
the owning integration by `page_id`, creates the `Lead`, and commits before the
endpoint reports success. *(That resolution took the first match; S5 made it
fail closed when more than one integration claims the page.)* Schema additions: a `webhook_events` table (unique on
`provider + event_id`, storing the raw payload, status and error) plus
`leads.external_id` and `leads.source_metadata`.

Response contract:

| Case | Response |
|---|---|
| Valid leadgen | `200` with `leads_created` |
| Duplicate retry | `200`, `duplicates_ignored`, no second lead |
| Invalid signature | `401`, nothing persisted |
| Malformed payload | `400` (retrying will not help) |
| Unroutable page | `200`, event retained as `unroutable` for replay |
| Ambiguous page (S5) | `200`, event retained as `ambiguous`, **no lead created** |
| Persistence failure | `500` so Meta retries |

**A real constraint worth knowing:** the Meta webhook carries only identifiers,
never the prospect's name or email — those require a Graph API call with the
page access token. When no token is stored or the lookup fails, the lead is
still persisted from the identifiers and marked
`contact_details_available: false` with an explicit `data_limitations` entry and
an `Unidentified Meta lead <id>` placeholder name. Contact details are never
invented to fill the gap.

**Verified:** 9 tests in `tests/test_meta_webhook.py` covering all six required
cases plus Graph API enrichment and non-leadgen events.

---

### P0-7 · Stop calling the rule-based lead scorer "AI" — **S** — ✅ IMPLEMENTED · TESTED · VERIFIED
`app/ai/orchestrator.py:55-56` always returns `self.lead_agent.deterministic_score(request)` and **never invokes the LLM**, while the UI presents it as AI lead scoring. The arithmetic itself is sound and explainable (`app/ai/agents/lead_agent.py:45-80`); the label is what is wrong.

Either route through `lead_agent.run()` when a real AI provider is configured, or rename the feature to "Lead Scoring" in the UI and describe the rules. Given your own no-fake-success rule, this is the one place the product currently overstates what it runs.

**Resolved — renamed rather than converted to an LLM call.** The system holds no
behavioural signals (page visits, email opens, form behaviour are not tracked),
so an LLM could only speculate about them. Routing through the model would have
traded an inaccurate label for an actual fabrication risk. The arithmetic was
already sound; only the naming was wrong.

- `orchestrator.score_lead()` → `score_lead_deterministic()` (the misleading
  alias is gone, and a test asserts it stays gone).
- `LeadScoreExplanation` now carries `method="deterministic_rules"`,
  `method_label`, `evidence[]` and `data_limitations[]`. Evidence cites only
  supplied field values; untracked behaviour is reported as a limitation ending
  in "Insufficient data."
- UI: "AI Lead Scoring" → "Lead Scoring", now stating "no AI model and no
  inferred browsing, email or form behaviour", and rendering a "Not assessed"
  list. "CRM with AI scoring" → "deterministic rule-based scoring".

**Verified:** 10 tests, including one that fails if any of nine fabrication
markers ever appear in reasons or evidence, and one that greps the frontend for
"AI Lead Scoring" / "AI scoring".

---

### P0-8 · Remove pre-filled demo credentials from the login page — **S** — ✅ IMPLEMENTED · TESTED · VERIFIED
`apps/web/src/app/(auth)/login/page.tsx:13-14` ships `demo@growthos.ai` / `demo1234` pre-populated. Gate behind `NEXT_PUBLIC_ENVIRONMENT=development`.

**Resolved.** Both fields start empty in every environment. No demo credential
literal remains anywhere in `apps/web/src`. A development-only "Fill demo
credentials" button renders only when `NEXT_PUBLIC_ENVIRONMENT=development` and
both `NEXT_PUBLIC_DEMO_EMAIL` and `NEXT_PUBLIC_DEMO_PASSWORD` are set; those ship
empty in `.env.example` and live in the gitignored `apps/web/.env.local`.

Remaining occurrences of the credentials are the backend test suite, the
development seeder and the README's development-login note — all correctly
scoped to development.

**Verified:** a production build (`NEXT_PUBLIC_ENVIRONMENT=production`) was
grepped — `.next/` contains neither `demo@growthos.ai`, `demo1234`, nor the
demo button, which is tree-shaken out entirely. Four tests guard the source.

---

### P0-9 · Create the initial Alembic migration and stop using `create_all` — **M** — ✅ IMPLEMENTED · TESTED · VERIFIED
`apps/api/alembic/versions/` is **empty** and `app/main.py:19` calls `Base.metadata.create_all` at startup. There is no versioned schema and no rollback path.

```bash
cd apps/api && alembic revision --autogenerate -m "initial schema" && alembic upgrade head
```
Gate `create_all` behind `DB_AUTO_CREATE` (dev only) and run migrations as a deploy step.
**Done when:** a fresh Postgres reaches the correct schema via `alembic upgrade head` alone.

**Resolved.** Revision `af352aece3bc` ("initial schema") covers all 40 tables and
was generated against real PostgreSQL, not SQLite. `create_all` is now gated by
`Settings.should_auto_create_tables`, which returns `False` in production
unconditionally; setting `DB_AUTO_CREATE=true` there fails startup rather than
being silently ignored. Migration runbook: [`docs/MIGRATIONS.md`](docs/MIGRATIONS.md),
deploy helper: `apps/api/scripts/migrate.sh`.

**Verified on a fresh PostgreSQL 16 container:**
- `alembic upgrade head` → 40 tables + `alembic_version`
- `alembic check` → "No new upgrade operations detected" (the migration matches
  the models exactly — no drift)
- `alembic downgrade base` → schema empty, then `upgrade head` again → clean
- **The full 122-test suite passes against the Alembic-created PostgreSQL
  schema**, not just against SQLite.

Existing pre-Alembic databases must be stamped once (`alembic stamp head`) —
documented in `docs/MIGRATIONS.md`. Downgrade is documented as a dev/staging
tool; production incidents should restore from backup and roll forward.

---

### P0-10 · Rotate every real secret before launch — **S** — ⛔ STILL OPEN (operational)
> Cannot be completed in code. It is now *enforced*, though: production refuses
> to boot with placeholder or weak secrets (P0-2), so a deploy that skips
> rotation fails loudly instead of running on a publicly known key.
`.env` files are correctly gitignored and no secrets are committed, but the current values are the shipped placeholders.

```bash
openssl rand -hex 32                                                   # SECRET_KEY
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"  # ENCRYPTION_KEY
```
Store them in your host's secret manager, not in a file.
**Note:** rotating `ENCRYPTION_KEY` after integrations exist makes stored OAuth tokens unreadable — plan a re-consent flow.

---

### P0-11 · Job claiming, leases and duplicate-execution safety — **M** — ✅ IMPLEMENTED · TESTED · VERIFIED
The old `process_due()` selected rows then set `status=running` with no locking,
so two workers could execute the same job, and a worker crash stranded the row
in `running` forever.

**Resolved.** Execution now requires an explicit claim: a compare-and-swap
`UPDATE` guarded by the row's current status, so racing workers produce exactly
one winner on both PostgreSQL and SQLite. On PostgreSQL the candidate scan also
uses `FOR UPDATE SKIP LOCKED` so workers take disjoint batches.

- Lease on claim (`locked_by`, `lease_expires_at`, `heartbeat_at`); an expired
  lease makes the job reclaimable instead of orphaned.
- `heartbeat()` renews, and only for the lease owner.
- `reap_expired_leases()` requeues recoverable jobs and explicitly fails those
  that exhausted their attempts, rather than leaving them looking in-progress.
- States: `QUEUED → RUNNING → COMPLETED | RETRYING | FAILED`, plus `CANCELLED`.
  `retry()` recovers failed jobs; `cancel()` stops queued/retrying ones.
- Retries use capped exponential backoff and release the lease on failure.
- *(S1 added tenancy: `process_due`, `claim`, `retry` and `cancel` take an
  optional `organization_id`, applied inside the claim `UPDATE` so scoping
  cannot be lost to a race. The worker still runs unscoped.)*

**Verified:** 18 tests in `tests/test_job_queue.py`, including eight concurrent
workers racing one job (exactly one handler invocation), batch partitioning
without overlap, crash recovery via expired lease, and owner-only heartbeat.
**Run against real PostgreSQL as well as SQLite**, since SQLite serialises
writes and would not have proven the locking on its own.

---

### P0-12 · Server-side role-based authorization — **M** — ✅ IMPLEMENTED · TESTED · VERIFIED
Invited members could approve and execute financial and campaign actions.

**Resolved.** `app/core/permissions.py` defines a four-role model — `OWNER`,
`ADMIN`, `MEMBER`, `VIEWER` (`viewer` is new) — with 15 permissions in strictly
nested sets. Unknown roles fall back to least privilege, not open access.

- `MEMBER` keeps day-to-day work (content, clients, leads) but **cannot**
  publish campaigns, change budgets, approve/execute actions, connect or
  disconnect integrations, or change autonomy settings.
- `VIEWER` is genuinely read-only.
- Billing and organization management are `OWNER`-only.

Enforcement is a `require_permission()` FastAPI dependency on **36 write
routes**, so it runs before the handler — never frontend button visibility.
*(S3 found eleven more spend-bearing routes — AI, media, sync, job processing —
that this pass had missed, and put them behind the same gate.)*

**Verified:** 41 tests in `tests/test_authorization.py` — the permission matrix,
end-to-end 403s for members and viewers across approve/execute/publish/connect/
disconnect/autonomy/autonomous-run, an admin passing the gate (404, not 403),
viewers retaining read access, and members still able to write clients and
leads. Two coverage-guard tests fail if a new money-moving route ships without a
permission dependency. A separate check confirms **no GET route** was gated.

---

## P1 — Production infrastructure

> **Numbering note.** The hardening plan renumbered P1 around infrastructure
> capabilities rather than around individual audit findings. The plan IDs below
> are authoritative; the original audit IDs are listed under
> "Original audit items" further down and cross-referenced.

| Plan ID | Capability | Status |
|---|---|---|
| P1-1 | Distributed rate limiting | ✅ IMPLEMENTED · TESTED · VERIFIED |
| P1-2 | S3-compatible object storage | ✅ IMPLEMENTED · TESTED · VERIFIED |
| P1-3 | Background worker / job system | ✅ IMPLEMENTED · TESTED · VERIFIED |
| P1-4 | Structured production logging | ✅ IMPLEMENTED · TESTED · VERIFIED |
| P1-5 | Global error handling | ✅ IMPLEMENTED · TESTED · VERIFIED |
| P1-6 | Token refresh lifecycle | ✅ IMPLEMENTED · TESTED · VERIFIED |
| P1-7 | Meta lead contact backfill | ✅ IMPLEMENTED · TESTED · VERIFIED |
| P1-8 | Usage metering | ✅ IMPLEMENTED · TESTED · VERIFIED |
| P1-9 | Billing foundation | ✅ IMPLEMENTED · TESTED · VERIFIED |
| P1-10 | Health checks (live / ready) | ✅ IMPLEMENTED · TESTED · VERIFIED |
| P1-11 | Monitoring metrics | ✅ IMPLEMENTED · TESTED · VERIFIED |
| P1-12 | Production configuration | ✅ IMPLEMENTED · TESTED · VERIFIED |
| P1-13 | Frontend production states | ✅ IMPLEMENTED · TESTED · VERIFIED |

---

### P1-1 · Distributed rate limiting — **M** — ✅ IMPLEMENTED · TESTED · VERIFIED
*Closes original audit items P0-5 (login brute force) and P1-7 (per-process counters).*

**What was wrong:** `app/security/rate_limit.py` was a per-process in-memory dict wired only into `get_current_auth`, so every unauthenticated endpoint — including `/auth/login` — was completely unlimited, and two uvicorn workers doubled every limit that did apply.

**What was built**

- `RateLimitBackend` with two implementations: `RedisRateLimitBackend` (atomic `INCR` + `EXPIRE` fixed window, shared across API instances) and `InMemoryRateLimitBackend` (development and tests).
- Production startup now **fails without `REDIS_URL`** (`app/core/startup_checks.py`), because per-process counters hand an attacker N times the budget on an N-instance deployment.
- Eight named policies, each configurable by environment variable: `general`, `auth_identity`, `auth_ip`, `ai`, `media`, `report`, `campaign_execution`, `webhook`.
- Credential endpoints enforce **two keys at once** — client IP and a SHA-256 hash of the submitted email. Rotating emails does not buy a fresh budget (the IP key holds), and hammering one account does not lock out other users (the identity key is separate).
- Expensive operations are keyed on the **organization**, so a member cannot get extra budget by switching user accounts and tenants cannot throttle each other. Attached as an extra route dependency (`app/security/limits.py`) so it composes with the existing `require_permission` gates.
- 429 responses carry `Retry-After`, `X-RateLimit-Limit`, `X-RateLimit-Remaining` and a generic body.
- Redis outage degrades to per-process counters (`RATE_LIMIT_DEGRADE_TO_LOCAL=true`, default) and logs an error, rather than taking authentication offline. Set false to fail closed.

**Account enumeration:** the limiter is a dependency that runs before any handler touches the database, so it cannot know whether an account exists and therefore cannot behave differently. `test_rate_limit_does_not_reveal_whether_an_account_exists` asserts byte-identical throttled responses for a real and a nonexistent account.

**Endpoints covered:** login, register, Meta webhook, `creative` image/video/variations, `autopilot` image/video/creative/variations/run/decision-loop/campaign build+propose/content publish/optimization analyze, report generate, strategy generate, content generate, assistant chat, recommendations generate, competitor analysis.

**Tests (17, `tests/test_rate_limiting.py`)** — budget exhaustion and key isolation; identifier hashing; **two Redis-backed instances sharing one budget** (proves multi-instance correctness); degraded and fail-closed behaviour; production startup refusing to boot without `REDIS_URL`; `401 ×5 → 429 ×3` on brute force; `Retry-After` present; IP budget holding across rotated emails; enumeration-safe responses; legitimate logins unaffected; registration limited; one throttled account not affecting a bystander; org media budget enforced and tenant-scoped; org budget surviving a user switch; a coverage guard asserting the sensitive routes still declare a limit.

**~~Known limitation~~ — closed by S2 below.** `X-Forwarded-For` was trusted whenever present, so a client could spoof its source IP and reset its own IP budget. The header is now honoured only from peers listed in `TRUSTED_PROXY_IPS`, and production refuses to boot without an explicit value.

---

### P1-2 · S3-compatible object storage — **M** — ✅ IMPLEMENTED · TESTED · VERIFIED
*Closes original audit items P1-1 (S3/R2 storage) and P1-10 (report PDFs on local disk).*

**What was wrong:** `get_object_storage()` returned `LocalObjectStorage` for *every* backend value including `s3`, so a production deployment configured for S3 wrote to an ephemeral container disk and reported every upload as successful. Report PDFs bypassed the abstraction entirely and were written straight to `STORAGE_LOCAL_PATH/reports`.

**What was built**

- `S3ObjectStorage` implementing the full interface: `upload`, `get_bytes`, `delete`, `exists`, `content_type`, `get_url` (presigned GET) and `health_check`. boto3 calls run in a thread so they do not block the event loop.
- **No provider is hard-coded.** `STORAGE_BACKEND` accepts `s3`, `r2`, `minio`, `wasabi`, `spaces`; the provider is selected by `S3_ENDPOINT_URL`. `S3_FORCE_PATH_STYLE` covers MinIO. Credentials may be omitted entirely to use an instance role, but setting only one half is rejected as a likely typo.
- **No silent fallback.** `get_object_storage()` raises `StorageConfigurationError` on an unknown backend, and on `local` in production. Production startup validation fails the same way, so the error arrives at boot rather than on the first upload.
- **Failure is never reported as success.** Every upload is followed by an existence check; `MediaGenerationService._persist()` marks the job `FAILED` with `STORAGE_UPLOAD_FAILED` and the underlying reason if either step fails. A backend that accepts a write and stores nothing is caught by the same check.
- **A storage outage is distinguished from a missing object.** `exists()` raises on a transport error instead of returning `False`, so a Redis/S3 hiccup cannot flip a completed job to `COMPLETED_WITHOUT_FILE`. Read endpoints return 503, not 404.
- **Ownership is enforced from the key.** Keys are `organizations/{org}/clients/{client}/...`; `key_belongs_to_organization()` is checked before bytes are served, so a tampered `storage_key` cannot read across tenants. Key segments are sanitised so an attacker-controlled value cannot inject path levels.
- **Report exports moved into storage.** `_render_pdf` returns bytes, `_store_pdf` uploads them and `export_path` now holds a storage key. A failed upload stores `None` rather than a path that would 404 later. Rows written before this change still resolve from disk.

**Tests (30, `tests/test_object_storage.py`)** — production refusing local storage and unknown backends; missing bucket; half-configured credentials; R2 endpoint honoured; startup validation; upload/download/delete/missing-object round trips against **moto** (a real S3 protocol implementation, exercising the production boto3 client config); presigned URL expiry and no leaked secret; bucket health check; upload failure raising; transport error distinguished from a missing object; media job failing on both storage error and silently-discarded write; key ownership including a prefix-confusion case (`organizations/abc-evil` must not match `abc`); traversal containment; an end-to-end HTTP test where a tenant with a forged `storage_key` gets 404; report export round trip, failure path, and cross-tenant refusal.

**Known limitation:** listing endpoints do not probe storage per row — a HEAD per asset would make listing 200 assets 200 round-trips. Existence is confirmed when bytes are actually read.

---

### P1-3 · Background worker / job system — **L** — ✅ IMPLEMENTED · TESTED · VERIFIED
*Closes original audit item P1-2 (no worker process; media generation inline).*

**What was wrong:** `process_due()` had no runner — nothing called it outside a request. Image and video generation executed inside the HTTP request, and video polled the provider for up to ~62 seconds before giving up, so a slow generation both blocked the caller and was lost on any API restart.

**What was built**

- `app/worker.py` — a runner over the existing PostgreSQL-backed queue. **No broker was added.** The queue already had atomic claiming, leases, backoff and lease recovery from P0-9; Celery or arq would have added an operational dependency without adding a capability. It runs from the same image with a different command (`python -m app.worker`) and is wired into `docker-compose.yml` as a `worker` service.
- Each cycle reclaims expired leases, claims a batch, executes it, and sleeps — skipping the sleep when the batch came back full so a backlog drains. Every cycle uses a fresh session, so one handler that corrupts its session cannot poison the next cycle.
- `SIGINT`/`SIGTERM` request a stop **after** the current cycle, so shutdown never abandons a claimed job.
- `app/jobs/registry.py` is the single job-type → handler map used by both the worker and the inline development path, so a handler cannot be reachable from one and not the other.
- Handlers: image generation, video submission, video polling, report generation, analytics sync, Meta lead backfill, publish-due.
- **Video submission and completion are separate jobs.** `media.generate_video` submits and returns; `media.poll_video` re-enqueues itself with growing delay until the provider finishes or `VIDEO_JOB_TIMEOUT_SECONDS` passes, at which point the job fails with `PROVIDER_TIMEOUT` rather than sitting in `PROCESSING` forever. A worker restart resumes polling instead of orphaning the job.
- **Idempotency:** `enqueue(dedupe_key=...)` returns the existing job instead of duplicating work, backed by a unique index so two API instances racing on a duplicate submit resolve at the database. Migration `b1a3c7d92f04`.
- **HTTP now returns a job id.** `POST /creative/videos/generate` and `/images/generate` enqueue and return `QUEUED`. New `GET /api/v1/jobs`, `GET /api/v1/jobs/{id}`, `POST /api/v1/jobs/{id}/retry` and `/cancel` are organization-scoped. Async variants added for reports (`POST .../reports/generate/async`) and analytics (`POST /integrations/{provider}/sync/async`), both 202 with a `poll_url`.
- `INLINE_JOB_EXECUTION` keeps single-process development usable; production ignores it and startup **fails** if it is set true.

**Tests (20, `tests/test_worker.py`)** — successful execution and lease release; retry scheduled with a delay; backoff demonstrably growing across three attempts; permanent failure at max attempts; unknown job type failing without burning retries; cancellation never executing; worker crash leaving an expired lease that a second worker reclaims; six concurrent workers executing one job exactly once; duplicate claim rejected; a poisoned job not blocking others; recovery from a session-corrupting handler; clean stop; **stop not abandoning an in-flight job**; dedupe idempotency and independence without a key; production never running inline and startup rejecting it; every registered type having a handler; an end-to-end HTTP test asserting the video endpoint returns `QUEUED` **without calling the provider**; job endpoints refusing cross-tenant access.

**Known limitation:** the worker polls (default 2s). A busy deployment can lower `WORKER_POLL_INTERVAL_SECONDS`, but latency-sensitive work would want a notification channel; polling was chosen to avoid new infrastructure.

---

### P1-4 · Structured production logging — **M** — ✅ IMPLEMENTED · TESTED · VERIFIED
*Partially closes original audit item P1-4 (logging, request IDs, global exception handler); the handler itself is P1-5.*

**What was wrong:** no logging configuration beyond the default, no correlation identifier, and no way to connect a user-reported failure to anything in the output. Nothing prevented a service from passing a token straight to `logger.info`.

**What was built**

- `app/observability/logging.py` — JSON one-object-per-line in production, human-readable in development. `LOG_FORMAT` overrides the default; `LOG_LEVEL` sets the level. Uvicorn's own loggers are re-parented so access lines are JSON too, rather than a second, unstructured format in the same stream.
- **Correlation.** `RequestContextMiddleware` honours an inbound `X-Request-ID` (edge proxies usually set one) or generates a UUID, stores it in a `ContextVar`, and echoes it on the response. Organization and user id are bound once authentication resolves, so post-auth lines are tenant-attributed without any call-site changes. The worker binds the same variables per job.
- **Redaction is structural.** A `logging.Filter` scrubs every extra field whose key matches a sensitive marker (`password`, `secret`, `token`, `api_key`, `authorization`, `credential`, `private_key`, `access_key`, `cookie`, `signature`) and recurses into nested dicts and lists, so a leak requires a *value* to be interpolated into a message rather than merely passed as context.
- **Failed logins log a hash of the email**, not the address, so the log is not an enumeration list. The hash is stable, so repeated attempts against one account still correlate.
- `app/observability/events.py` — consistent field names for auth success/failure, authz denial, AI generation, media generation, storage error, integration sync, webhook received, campaign execution, database error. Wired into the auth endpoints, the permission dependency, the media generation service, the integration service, the Meta webhook, the rate limiter and the worker.
- One request line per request with method, path, status and duration.

**Tests (32, `tests/test_logging.py`)** — JSON envelope and field passthrough; a log line stays a single parseable object when the message contains newlines; exception text and traceback reach the log; format defaults per environment; 15 parametrised sensitive key spellings recognised; nested redaction; **a secret passed as a field is absent from the rendered output while neighbouring non-sensitive context survives**; failed login logs a hash and not the address; hash stability; context variables attached to records; response carries a request id; an inbound id is reused; a 5 KB id is truncated to 64 characters; consecutive requests get distinct ids; the request line records method/path/status/duration; auth success and failure events emitted through real HTTP logins; an authorization denial logs role and permission; a rate-limit rejection logs the scope but **not** the client IP.

**Deliberately not done:** no Sentry or vendor SDK. Logs are structured and provider-independent; shipping them is a deployment decision, and P1-11 exposes metrics on the same principle.

---

### P1-5 · Global error handling — **M** — ✅ IMPLEMENTED · TESTED · VERIFIED
*Closes the remainder of original audit item P1-4 (global exception handler).*

**What was wrong:** only `AIGenerationError` and `AIProviderConfigurationError` had handlers. Anything else produced Starlette's bare `Internal Server Error`, or FastAPI's `{"detail": …}` containing whatever string the raise site used — for a `SQLAlchemyError` that is the driver message, which includes the failing SQL and sometimes the row values.

**What was built**

- `app/core/errors.py` — one envelope for every failure: `{"error": {"code", "message", "request_id"}}`. `code` is the stable contract; `message` is display text. A top-level `detail` mirror is retained so clients written against the old shape keep working.
- Handlers for: request validation, `AppError` (new base class with `ProviderError` and `JobError`), `HTTPException`, AI configuration and generation, storage configuration / unavailable / generic, `IntegrityError` → 409, `OperationalError` → 503, `DBAPIError`, `SQLAlchemyError`, and a catch-all `Exception`.
- **The catch-all says nothing.** Fixed message, fixed code; the exception type, its arguments and the traceback go to the log correlated by the same request id the caller was shown.
- **Validation errors drop pydantic's `input` field.** It contains the rejected value, so returning it verbatim would echo a mistyped password back to the client and into any client-side error reporting. Field name, message and type are returned; the value is not.
- Existing raise sites that encode a code in the string (`HTTPException(400, "BUDGET_REQUIRED: …")`) have it lifted into `error.code` automatically, so no call site had to be rewritten. `Retry-After` and other headers survive the translation.
- **Defect found by the tests:** an unhandled exception unwinds past `RequestContextMiddleware`, which resets the correlation ContextVar on the way out — so the traceback was logged with `request_id=None`, making the one line that matters unfindable from the id the user quotes. Handlers now restore the id from `request.state`, and error responses set `X-Request-ID` themselves rather than relying on the middleware they bypassed.
- **Frontend:** `ApiError` carries `code`, `requestId`, `fields` and `isRetryable`; `errorMessage()` maps codes to user-facing copy. A non-JSON body (proxy timeout, gateway HTML) no longer produces `"Request failed"` with no context.

**Tests (37, `tests/test_error_handling.py`)** — a fixture app raises one representative exception per class. Envelope shape across 7 failure classes; request id matching the response header; a support-supplied id flowing into the body; the legacy `detail` mirror; generic 500 body; **a database DSN embedded in an exception absent from the response for three different failure paths**; no traceback, exception type or file path in any body; SQL and row values absent from a conflict response; **a submitted password absent from a validation error** while the offending field is still named; the traceback logged *with* the request id and *with* the secret (server-side is where it belongs); a database event emitted; 11 parametrised code/status mappings; embedded code lifting; storage outage as 503 rather than 404; AI failure naming the provider without leaking the provider's message; plus three tests against the real app including a rate-limit response that keeps `Retry-After`.

**Follow-up:** four existing tests asserted on `json()["detail"]` containing a code; they now assert `json()["error"]["code"]`, which is what the code was always meant to be.

---

### P1-6 · Refresh token lifecycle — **M** — ✅ IMPLEMENTED · TESTED · VERIFIED
*Closes original audit item P1-5 (authentication lifecycle).*

**What was wrong:** login returned a `refresh_token` that no endpoint accepted, so a session ended hard when the access token expired after 60 minutes. Worse, it was a signed JWT: nothing was stored server-side, so logout only removed the browser's copy and the token stayed valid for its full 14 days. There was no revocation, no rotation and no way to end a session.

**What was built**

- `app/models/auth_tokens.py` + migration `c4e18f7b2a55` — a `refresh_tokens` table holding **only the SHA-256** of each token, plus `family_id`, `expires_at`, `revoked_at`, `revoked_reason` and `replaced_by_id`. A database dump therefore yields no usable session, for the same reason passwords are hashed.
- **Opaque tokens, not JWTs.** 48 bytes from the system CSPRNG. Validity is a database question, which is what makes revocation possible at all.
- **Rotation.** Refresh consumes the presented token and issues a successor in the same family, so each token has exactly one legitimate use.
- **Reuse detection.** Presenting an already-rotated token revokes the entire family. Theft-and-replay and a client replaying are indistinguishable, so both end the session; other sessions belonging to the same user are untouched, so a compromised phone does not sign the user out of their laptop.
- Endpoints: `POST /auth/refresh`, `POST /auth/logout` (idempotent, never 401s, never reveals whether the token existed), `POST /auth/logout-all`, and `GET /auth/sessions` so a user can see and end their live sessions.
- **Uniform rejection.** Expired, revoked, reused and never-existed all return the same 401 body; the specific reason goes to the log only.
- Deactivating a user revokes their family on the next refresh attempt, so `is_active=False` cannot leave a working session.
- **httpOnly cookie** scoped to `/api/v1/auth`, `Secure` outside development, `SameSite` configurable — browser code need never hold the token. The body copy remains for non-browser clients. *(S4 later made this the only browser path: the body copy is now opt-in and refused when the cookie is present.)*
- **Frontend:** a 401 triggers one refresh-and-retry before redirecting to login, which is what actually fixes the 60-minute logout. Concurrent 401s share a single in-flight refresh — two parallel rotations would look exactly like reuse and revoke the session. Sign-out calls the endpoint instead of only clearing local storage.

**Defect found by the tests:** the reuse-detection revocation was being **rolled back**. `get_db` rolls the session back on an exception, and rejection raises, so the family revocation was undone on the way out and the attacker's successor token kept working — the feature would have looked correct in a unit test and done nothing in production. `/auth/refresh` now commits before raising, verified by an HTTP-level test that replays a rotated token and then asserts the successor is dead.

**Tests (28, `tests/test_refresh_tokens.py`)** — raw token never stored; token is not a decodable JWT; httpOnly cookie flags; `Secure` per environment; valid refresh returns a new pair; the new access token works; the old token dies; cookie-only refresh; a 3-step rotation chain staying in one family with exactly one live token; invalid / missing / expired / revoked rejected; **all rejection bodies byte-identical**; deactivated user cannot refresh and is left with no live session; family revocation on reuse; **reuse revocation surviving the rejected request**; other sessions unaffected; reuse logged with the user id; logout revoking and clearing the cookie; logout idempotent; logout-all ending every session; the sessions list showing only live sessions and not crossing users; refresh lifetime exceeding access lifetime; access token claims; expired-row purge.

**Migration note:** existing JWT refresh tokens have no row and are rejected, so every signed-in user re-authenticates once. The alternative was honouring unrevocable tokens for another 14 days.

---

### P1-7 · Meta lead contact backfill — **M** — ✅ IMPLEMENTED · TESTED · VERIFIED

**What was wrong:** P0-6 made the webhook persist leads with an explicit note when contact details were unavailable, which was the important half. The missing half was recovery: nothing ever tried again, so a lead ingested while the token was expired stayed a bare identifier permanently, and there was no list of which leads were in that state.

**What was built**

- `app/services/lead_backfill_service.py` — one retrieval attempt for one lead, with `enrichment_status` ∈ `complete` / `pending` / `failed` / `unavailable`, an attempt counter and a last-attempt timestamp on `source_metadata`.
- **A retry only adds.** It replaces the `Unidentified Meta lead …` placeholder created at ingest, but never a name a human has corrected and never an email already present. A later provider fetch cannot destroy better information.
- **What counts as a limitation was narrowed.** If the Graph API answers and returns no phone number, the form did not collect one — flagging that as missing data forever is noise. After a successful retrieval only a lead that is still uncontactable or still unnamed is flagged; when retrieval did *not* happen, every empty field is genuinely unknown and each is named individually (`Missing from this lead: email address, phone number, name.`) rather than a blanket "incomplete".
- **A missing token is terminal, not retried.** Backoff cannot fix an unconfigured integration, so the state is recorded and the endpoint reports `unavailable` rather than a "queued" that will never succeed. Transient errors propagate so the worker's backoff applies.
- Endpoints: `GET /clients/{id}/leads/awaiting-contact`, `POST /clients/{id}/leads/{lead_id}/backfill` (runs inline so the caller learns the real outcome), `POST /clients/{id}/leads/backfill` (bulk, queued). Queued backfills dedupe per lead, so repeatedly pressing the button does not stack Graph API calls.
- Ingest and retry now share one vocabulary for missing data, so a lead reads the same however it reached that state.

**Tests (25, `tests/test_lead_backfill.py`)** — a failed lookup leaves email, phone and name untouched; the failure names exactly which fields are missing; **an empty Graph response does not produce a contact**; a missing token is recorded rather than retried; a successful backfill writes the real values and clears the limitations; partial data stays partial and says which field is still missing; first + last name combined; an activity row recorded; **a human-corrected name and an existing email are not overwritten**; a complete lead never calls the provider; repeated backfill is stable; attempts counted; transient failure propagates for retry; double-queue produces one job; the worker handler performs a real backfill; only incomplete Meta leads are selected; manual leads are never selected; cross-tenant refusal at both the service and the endpoint; and the three HTTP endpoints.

**Not done (correctly):** no `leads_retrieval` permission check before calling — Meta reports that as an API error, which is handled as a failure with the reason recorded rather than guessed at in advance.

---

### P1-8 · Usage metering — **M** — ✅ IMPLEMENTED · TESTED · VERIFIED

**What was wrong:** nothing recorded consumption. There was no basis for an invoice, no way to enforce a plan limit, and no way to answer "why is this organization costing so much".

**What was built**

- `app/models/usage.py` + migration `d72f4b9c1e08` — `usage_records`, **one row per event, not a running total**. A counter incremented wrongly is wrong forever; rows can be inspected and a billing dispute settled.
- **Idempotency is a unique column.** Writers supply a key derived from the event — `image:{asset_id}`, `video:{asset_id}`, `lead:{lead_id}`, `report:{report_id}` — so a retried job records once. AI calls deliberately use a fresh key each time: a retried job genuinely calls the provider again and is genuinely charged again, so deduplicating there would under-bill.
- Metrics: `ai_request`, `ai_tokens`, `image_generation`, `video_generation`, `report_generation`, `storage_bytes`, `integration_sync`, `lead`, `client`. Clients and storage are **standing totals** (a plan caps how many you may *have*); the rest reset per month.
- `period` is stored, not derived at read time, so a late-arriving record lands in the month it happened.
- **No pricing anywhere.** A test walks the module's AST and fails on any identifier containing `price`, `cost`, `usd`, `currency` — checking identifiers, not prose, so the module may still explain itself in a comment.
- **AI metering without a refactor.** Services call `get_orchestrator()` with no tenant argument; threading one through a dozen call sites was out of proportion. The provider is wrapped (`MeteredProvider`) and reads the organization from the request context that auth already binds. Token counts come from the provider's own `usage` block and are left absent rather than estimated.
- Endpoints: `GET /usage`, `GET /usage/{metric}/records` (the events behind a total), `GET /usage/period`. All organization-scoped.

**Performance defect found and fixed:** the first implementation wrote each record immediately, opening a second connection while the request's transaction was still open. The test suite went from 42s to 95s — lock contention, and in production extra pool pressure. Usage is now buffered in a ContextVar per request or job and flushed once afterwards on its own session, which also makes it correct: consumption happened whether or not the request rolled back. Runtime returned to 42s.

**Tests (24, `tests/test_usage_metering.py`)** — same key records once; **a retried job billed once across three attempts**; distinct events counted separately; a duplicate never breaks the caller; an unknown metric is swallowed rather than failing a request; usage with no organization is discarded rather than attributed; isolation between organizations; client attribution recorded while usage belongs to the org; monthly bucketing; the period stored not derived; standing totals for clients; summary aggregation with unused metrics reported as zero rather than absent; the no-pricing AST check; fractional quantities; buffer queue/flush/empty-flush and the no-buffer fallback; a real AI call metered against the request's organization and **not** attributed when there is no tenant context; client creation metered end to end; and four HTTP tests including cross-tenant invisibility.


---

### P1-9 · Billing foundation — **M** — ✅ IMPLEMENTED · TESTED · VERIFIED
*Closes original audit item P1-9 (per-organization spend caps).*

**What was wrong:** the only subscription record was `Subscription` in `app/models/ai_ops.py` — a plan name as a bare string, written at registration and read by nothing. There was no lifecycle, no limit, and nothing stopping one organization from running an unbounded provider bill against your account.

**What was built**

- `app/models/billing.py` + migration `e83a5c2d7b16` — three tables. `plans` holds limits and feature flags as JSON keyed by usage metric, so a new metered resource needs no migration. `organization_subscriptions` holds the lifecycle. `billing_events` is append-only history, because billing questions are almost always historical ("why was I downgraded") and a mutable status column cannot answer them. The inert `subscriptions` table is left untouched.
- **Five states**, with `PAST_DUE` deliberately distinct from `CANCELLED`. A failed charge is usually an expired card; cutting access at the moment a charge bounces loses accounts that would have paid. `PAST_DUE` stays usable for a 7-day grace window enforced by `grace_period_ends_at`, then expires.
- **Status is a string, not a native PostgreSQL enum.** Adding a state to a native enum requires DDL, and billing lifecycles gain states.
- Four seeded plans (`free`, `starter`, `growth`, `agency`). **A metric absent from a plan's `limits` is unlimited** — a plan that forgot to mention a limit should not accidentally block a paying customer. Per-organization `limit_overrides` beat the catalogue for negotiated deals.
- **Enforcement reads the P1-8 meter**, not a second counter. "You have used 10 of 10 images" is derived from the recorded events, so the number in the error and the number on the invoice cannot drift apart.
- `app/security/quota.py` — `requires_quota(metric)` and `requires_feature(flag)` as route dependencies, composing with the existing rate limit and permission gates. They run **before** the expensive work. **402**, not 403: the caller is authenticated and permitted, the plan simply does not cover it, and the fix is a payment rather than a role change.
- Enforced on: image generation (quota), video generation (feature + quota), report generation both sync and async, client creation, and the AI surfaces (assistant chat, content, strategy).
- **No payment is taken and none is faked.** `PaymentProvider` is a Protocol with one implementation, `UnconfiguredPaymentProvider`, whose every method raises. A stub returning a plausible customer id would let the system report a subscription no provider knows about. `POST /billing/plan` is the administrative half only and says so. The provider reference columns hold opaque ids, useless without the provider's API key, which stays in configuration.
- Endpoints: `GET /billing/plans`, `/billing/subscription`, `/billing/quotas` (limit and consumption together, so a UI can warn before a 402), `/billing/events`, and `POST /billing/plan` gated on `billing_manage` (owner only).
- Frontend: `ApiError.isPlanLimit` distinguishes a 402 from a retryable failure, so the UI offers an upgrade rather than a retry button. The server's quota message is specific ("your free plan allows 10…") and is shown verbatim rather than replaced with generic wording.

**A refusal is committed, not rolled back.** `limit.exceeded` is recorded before the exception is raised, and the dependency commits it before converting to a 402 — otherwise the request rollback would take the evidence behind an upgrade prompt with it. Same failure mode as the P1-6 revocation bug.

**Tests (43, `tests/test_billing.py`)** — catalogue seeding is idempotent; no price column exists on a plan; a missing subscription becomes a trial and reading twice does not create two; every transition recorded and a repeat not double-logged; a lapsed trial and an exhausted grace window expire on read while a running trial is left alone; a failed payment does **not** immediately block work; paying clears the grace deadline; expired and cancelled both block; plan changes record the previous plan; an unknown plan is refused; a quota counts real recorded usage; the request that would cross the line is the one refused; an absent metric is unlimited; a zero limit blocks the first attempt; an upgrade lifts the ceiling; an override beats the plan; **another tenant's usage does not count**; a refusal survives as an event; storage enforced as a standing total; unknown features default to denied; the payment provider raises on all three methods; no provider secret is stored or serialized; and eleven HTTP tests including 402 with `QUOTA_EXCEEDED` on image generation, `FEATURE_NOT_IN_PLAN` on video, the free plan's single-client limit, owner-only plan changes, and an in-quota request still succeeding.

**Verified on PostgreSQL:** migration applies to a fresh database, downgrades cleanly, and the billing and usage suites (67 tests) pass against real Postgres.

**Not built:** payment processing, invoicing, proration, dunning, a customer portal. Those are P2-1 and need a provider account.

---

### P1-10 · Health checks — **S** — ✅ IMPLEMENTED · TESTED · VERIFIED

**What was wrong:** one `/health` endpoint returning a static `{"status": "ok"}`. It could not tell an orchestrator whether the instance could actually serve, and there was nothing for a load balancer to key off.

**What was built** (`app/api/health.py`, documented in `docs/HEALTH_CHECKS.md`)

- **`GET /health/live`** — process liveness, touching **nothing external**. This is the whole point of the split: a failing liveness probe gets the container killed, so if liveness checked the database, a database outage would restart every API pod in a rolling loop — and since restarting an API pod does not fix a database, the loop outlives the outage while discarding every warm connection pool.
- **`GET /health/ready`** — checks `database`, `configuration` (the P0 startup guards), `queue` (the `background_jobs` table is queryable, so a worker's claim query can run), `object_storage` (the configured backend answers), and `rate_limit_backend`. 200/`ready` or 503/`not_ready`.
- **Redis is required only in production.** Outside it, the in-process limiter is a legitimate choice, so a missing Redis lands in a `degraded` list without pulling the instance from the pool.
- Checks run **concurrently, each with its own 3s timeout**. A probe that hangs is indistinguishable from a dead process to most orchestrators, so one wedged dependency must not hang the probe.
- The body lists **every** check even on failure, so an operator sees which dependency is down without opening the logs.
- Failure detail is the exception **type only**. Connection errors routinely carry a DSN with credentials, and this endpoint is unauthenticated; the full error and traceback go to the log under `event: health.check_failed`.
- Unversioned and unauthenticated — infrastructure should not track an API version to probe a pod. The original `/health` still answers for existing probes.
- `Dockerfile` gained a `HEALTHCHECK` and compose an `api` healthcheck, both pointing at **liveness**, not readiness.

**Tests (21, `tests/test_health.py`)** — liveness answers unauthenticated and reports uptime; **liveness stays green with the database dead and with storage dead**; a construction-level assertion that liveness invokes no dependency check at all; readiness passes when healthy and enumerates every check; 503 on a dead database, unreachable storage, and invalid configuration; healthy checks still listed alongside a failed one; a DSN with a password in the exception text does not reach the response; a hung dependency becomes a timeout rather than a hang; four 0.3s checks complete in under 0.9s, proving concurrency; an optional dependency is degraded rather than failed; the same dependency is required under production settings; the queue check reaches the job table; and the legacy `/health` still answers while `/api/v1/health/live` correctly 404s.

---

### P1-11 · Monitoring metrics — **M** — ✅ IMPLEMENTED · TESTED · VERIFIED

**What was wrong:** `MonitoringAgent` was fully implemented but never invoked. More importantly, there were no infrastructure metrics at all — request counts, latency, job failures, AI/media/storage/integration failures were invisible.

**Role clarification (deliberate):** `MonitoringAgent` is a *campaign health analyst*, not infrastructure monitoring. Conflating the two is why it sat unused. It is now wired through `OptimizationService.health_narrative()` (`GET /autopilot/campaigns/health/summary`): arithmetic scores stay deterministic; the LLM only narrates them. A provider outage still returns the real scores with `narrative_available=false` — never a fabricated overview.

**What was built**

- `app/observability/metrics.py` — in-process counters and latency histograms, provider-independent. Exposed as Prometheus text (`GET /metrics`) and JSON (`GET /metrics.json`).
- Recording hooks on real events: HTTP requests (middleware), jobs (worker), AI calls (`MeteredProvider`), media generation, storage, integration sync, rate limits, auth, database errors.
- A metric never observed is **absent**, not zero — reporting 0 would assert activity that never happened.
- Production requires `METRICS_TOKEN`; the endpoint sits outside the authenticated API and would otherwise leak traffic shape. Token comparison is constant-time.

**Tests (26+, `tests/test_monitoring.py`)** — registry absence-vs-zero; counters move only after real HTTP / job / auth / AI / media paths; Prometheus and JSON export; metrics token required in production; unauthorized scrape rejected; MonitoringAgent schema proven campaign-only; narrative never overrides arithmetic scores; provider outage preserves real scores.

---

### P1-12 · Production configuration — **S** — ✅ IMPLEMENTED · TESTED · VERIFIED

**What was wrong:** `.env.example` lagged the enforced startup guards (no `METRICS_TOKEN`, incomplete worker docs, billing still labelled "not yet implemented").

**What was built**

- `.env.example` updated with every production-required variable and the full fail-fast list: secrets, `DEMO_MODE`, AI provider, Postgres, Redis, S3, `INLINE_JOB_EXECUTION`, `DB_AUTO_CREATE`, `METRICS_TOKEN`, CORS.
- Refresh-cookie, worker, and rate-limit knobs documented.
- Billing section corrected: plan/subscription foundation exists; payment processing remains P2 and must not be assumed from Stripe env vars.
- `tests/test_production_config.py` — each production guard fails independently; development remains permissive; staging requires real secrets; `.env.example` is asserted to document every required name and to ship no demo password.

---

### P1-13 · Frontend production states — **M** — ✅ IMPLEMENTED · TESTED · VERIFIED

**What was wrong:** Creative Library treated image/video generation as a single blocking request and could imply success from a bare response. Integration statuses omitted `connecting` / `disconnected`. Campaign lifecycle was not spelled out in the builder UI.

**What was built**

- `apps/web/src/lib/jobs.ts` — `pollMediaJob` / `pollBackgroundJob`, media phases (`idle` → `queued` → `generating` → `processing` → `uploading` → `completed` | `failed`), campaign lifecycle helpers, integration lifecycle constants.
- Creative Library submits, shows phase badges, polls `/creative/{images|videos}/jobs/{id}`, offers Retry on failure, and only claims COMPLETED when the server returns that status with stored assets.
- Integrations page sets optimistic `connecting` on OAuth start and `disconnected` after disconnect; StatusDot covers the full set.
- Campaign Builder states the draft → pending approval → approved → executing → published/failed lifecycle and that live publish requires platform confirmation.
- AI Activity maps action statuses onto the same lifecycle labels.

**Tests (5, `tests/test_frontend_states.py`)** — source guards that polling, lifecycles, connecting/disconnected, and the no-fake-publish wording remain. Frontend `tsc`, lint, and production build all green.

## P1 — Original audit items

### P1-1 (audit) · Implement S3/R2 object storage — **M** — ✅ CLOSED BY P1-2
`app/storage/object_storage.py:95-101` falls back to `LocalObjectStorage` for *every* backend value, including `s3`. Container disks are ephemeral, so **all generated media is lost on redeploy**.

Add `S3ObjectStorage` implementing the existing `ObjectStorage` interface (`upload`/`get_bytes`/`exists`/`delete`/`get_url`) and make `get_object_storage()` raise on an unimplemented backend rather than silently degrading. Keep the existing key layout — it maps directly onto S3 prefixes.
**Done when:** media generated before a redeploy is still served afterward.

---

### P1-2 (audit) · Add Redis and a real worker process — **L** — ✅ CLOSED BY P1-1 + P1-3
`app/jobs/queue.py` has no runner: `process_due()` is only reachable from `app/jobs/handlers.py:70`. Media generation executes **inline in the HTTP request** (`media_generation_service.py:83`), and video polls for up to ~62s (`:328-341`) before giving up. An API restart orphans the job permanently.

- Add `redis` + `arq` (or Celery) to `apps/api/requirements.txt`
- Run the worker from the **same image** with a different command
- Move image and video generation into worker tasks; the API only enqueues and returns `QUEUED`
- Add retries with backoff and a dead-letter path
- Add a **job lease and recovery**: `app/jobs/queue.py:64-67` sets `running` before the handler executes, so a crash strands the row forever. Claim rows with `SELECT ... FOR UPDATE SKIP LOCKED` and reclaim expired leases, otherwise two workers can double-process
- Mount a volume for `./storage` in `docker-compose.yml` until P1-1 lands, so local media survives container recreation

**Done when:** a video job submitted immediately before an API restart still completes, and a killed worker's job is reclaimed rather than stranded.

---

### P1-3 (audit) · Add a Replicate completion webhook — **M**
Only polling is implemented (`app/generation/replicate_video.py:147`). Real video takes minutes; polling is fragile and wasteful. Replicate supports a completion webhook — add a signature-verified endpoint alongside the existing `app/api/v1/webhooks.py`, keeping polling as reconciliation.

---

### P1-4 (audit) · Add logging, a global exception handler, request IDs, and Sentry — **M**
There is **no `logging` import anywhere in `apps/api/app`** and no exception handler. A production 500 is currently untraceable.

- Structured JSON logging (`LOG_FORMAT=json`), one line per request with method, path, status, duration, `organization_id`, request ID
- Request-ID middleware, echoed as `X-Request-ID`
- Global handler returning `{"detail": "Internal error", "request_id": ...}` — never a stack trace when `DEBUG_ERRORS=false`
- Sentry via `SENTRY_DSN`, with secrets scrubbed

---

### P1-5 (audit) · Fix the authentication lifecycle — **M** — ⚠️ PARTIALLY CLOSED BY P1-6 + S4
Defects 1 and 2 are closed: P1-6 built the rotating, revocable refresh lifecycle and S4 moved the token out of JavaScript's reach entirely. **Defect 3 — password reset and email verification — remains open** and is a launch requirement.

Three related defects:

1. **Refresh is broken.** `auth_service.py:57,71` issues a refresh token but **no `/auth/refresh` endpoint exists**. The frontend stores it (`apps/web/src/lib/api.ts:16-19`) and never uses it — users are hard-logged-out after 60 minutes.
2. **No logout / revocation.** No `jti`, no denylist. A stolen token stays valid for its full lifetime.
3. **No password reset or email verification.**

Add `/auth/refresh` (with rotation), `/auth/logout`, a `jti` claim plus a Redis denylist, and wire automatic refresh-on-401 into the frontend `api()` helper.

---

### P1-6 (audit) · Add role-based authorization — **M** — ✅ CLOSED BY P0-12 + S3
The permission system landed with P0-12 (financial and destructive endpoints); S3 extended it to every endpoint that spends money or has an external consequence, and added an ADMIN/MEMBER/VIEWER regression matrix over 22 of them.

`MemberRole` exists but is enforced in exactly **one** place (`auth_service.py:110`, the demo-mode toggle). Any invited `member` can today delete clients, approve financial actions, and execute campaigns.

Add a `require_role("owner","admin")` dependency and apply it to client delete, autonomy settings, integration connect/disconnect, action approve/execute, and budget changes. Add an invite flow that can assign a non-owner role.
**Done when:** a `member` receives 403 on financial and destructive endpoints.

---

### P1-7 (audit) · Make rate limiting shared across instances — **S** — ✅ CLOSED BY P1-1
`app/security/rate_limit.py:18` is a per-process in-memory dict. With two uvicorn workers the effective limit doubles, and a restart clears it. Back it with Redis when `REDIS_URL` is set.

---

### P1-8 (audit) · Add security headers and enforce HTTPS — **S**
Runtime-verified absent: HSTS, X-Frame-Options, X-Content-Type-Options, CSP, Referrer-Policy. Add middleware in `app/main.py`, redirect HTTP→HTTPS at the edge, and confirm `API_CORS_ORIGINS` never contains `*` (credentials are enabled).

---

### P1-9 (audit) · Enforce per-organization AI spend caps — **M** — ✅ CLOSED BY P1-8 + P1-9
Nothing limited AI or media spend. Consumption is now recorded per organization (P1-8) and plan limits are enforced before the provider is called (P1-9), returning 402 with a specific quota message. Caps are expressed in units consumed rather than currency; a monetary ceiling needs provider pricing and belongs with P2-1.

---

### P1-10 (audit) · Route report PDFs through object storage — **S** — ✅ CLOSED BY P1-2
`app/services/report_service.py:205` writes PDFs to `Path(settings.storage_local_path)/"reports"`, bypassing the storage abstraction. They vanish on redeploy. Use `get_object_storage()` and serve through an authenticated, org-scoped endpoint.

---

### P1-11 (audit) · Containerize the frontend and add CI — **M**
There is **no `apps/web/Dockerfile`** and **no `.github/workflows`**. Either deploy the frontend to Vercel or add a multi-stage Dockerfile using Next.js standalone output. Add CI running the existing 15 tests, `tsc --noEmit`, and a cross-tenant isolation test on every push.

---

### P1-12 (audit) · Harden the API container — **S**
`apps/api/Dockerfile` runs as root, single-stage, with no healthcheck. Add a non-root user, a multi-stage build, and a `HEALTHCHECK` against `/health`. Add a readiness probe that verifies database and Redis connectivity.

---

## S — Security remediation (independent review, 2026-08-11)

Findings from [`INDEPENDENT_PRODUCTION_REVIEW.md`](INDEPENDENT_PRODUCTION_REVIEW.md).
All five are closed; the detail is in
[`PRODUCTION_READINESS.md` §0.1](PRODUCTION_READINESS.md).

| ID | Severity | Finding | Status |
|---|---|---|---|
| S1 | **CRITICAL** | `POST /autopilot/jobs/process` executed the global job queue, so one tenant could run another tenant's jobs | ✅ FIXED · TESTED · VERIFIED |
| S2 | HIGH | `X-Forwarded-For` trusted unconditionally; IP rate limits bypassable | ✅ FIXED · TESTED · VERIFIED |
| S3 | HIGH | Viewers could trigger AI, media, sync and job-processing endpoints | ✅ FIXED · TESTED · VERIFIED |
| S4 | HIGH | Refresh token returned to the browser and stored in `localStorage` | ✅ FIXED · TESTED · VERIFIED |
| S5 | HIGH | Meta `page_id` resolved to the first matching organization | ✅ FIXED · TESTED · VERIFIED |

**Tests: 447 → 581**, green on SQLite and on PostgreSQL 16 against the
Alembic-created schema. New files: `tests/test_tenant_isolation.py` (25),
`tests/test_proxy_trust.py` (20), `tests/test_meta_page_routing.py` (12);
`tests/test_authorization.py` grew to 108 and `tests/test_refresh_tokens.py`
to 35.

### S1 · Organization-scoped job execution
`JobQueue.process_due/claim/retry/cancel` take an `organization_id` that is
applied inside the claim `UPDATE`; handlers re-check that the record in the
payload belongs to the job's tenant; the endpoint requires `campaign_publish`
and only enqueues when a worker is deployed. The worker itself stays unscoped,
by design, and a test asserts it still drains every tenant.

### S2 · Trusted-proxy configuration
`TRUSTED_PROXY_IPS` (unset / `none` / IP-CIDR list / `*` for development).
The forwarded chain is walked right-to-left past our own hops. Production fails
to start when the value is unset, `*`, or unparseable.

### S3 · RBAC on expensive operations
Eleven endpoints moved from bare authentication onto the existing
`require_permission()` gate. Regression matrix covers ADMIN / MEMBER / VIEWER
for 22 endpoints, plus a check that viewers keep their read access.

### S4 · Refresh token out of JavaScript reach
Browsers get the httpOnly cookie only. Body delivery is opt-in for non-browser
clients and is refused when the request presented the cookie, which is the XSS
path. A test greps `apps/web/src` for any write of the token to web storage.

### S5 · Deterministic Meta page routing
Exactly one matching integration routes; zero is `unroutable`; more than one is
quarantined as `ambiguous` with no lead created. Connecting a page another
account already claims returns 409.

**Remaining launch requirements recorded during this pass** (deliberately not
implemented — they are not security fixes required by the above):

| Item | Where it is tracked |
|---|---|
| Password reset + email verification | P1-5 (audit) above, and P2-9 for delivery |
| CI pipeline (tests, `tsc`, lint, build, Postgres) | P1-11 (audit) above |
| Billing / payment provider | P2-1 below |
| Live ad publishing (Meta / Google Ads) | P2-4 and P2-5 below |

---

## P2 — Launch requirements

> **Milestone 8 note (2026-08-27):** CI, security headers, non-root API image,
> prod compose template, and ops runbooks landed. See
> [`docs/PRODUCTION_TODO.md`](docs/PRODUCTION_TODO.md) for the short M8 follow-up
> list. Live Meta/Google verification remains **PENDING**; do not enable
> `AUTONOMOUS_EXECUTION_ENABLED` / `CANARY_ENABLED` for a generic deploy.
>
> **Status — P2-A (AI creative & campaign engine) completed 2026-08-11.** It is a
> capability item rather than one of the numbered launch blockers below, and it
> closes none of them: no payments are taken and nothing is published. It is
> tracked here as **P2-A** with the remaining work it exposed.
>
> | Item | Status | Verification |
> |---|---|---|
> | P2-A strategy / brief / copy / concepts | **VERIFIED** | 35 tests in 3 new files; distinct-hypothesis and no-fabrication asserted directly |
> | P2-A real image generation | **IMPLEMENTED, VENDOR UNVERIFIED** | pipeline, storage, retrieval and failure paths tested; no vendor key in this environment |
> | P2-A real video generation | **IMPLEMENTED, VENDOR UNVERIFIED** | submit → poll → download → store tested with a fake provider; no vendor key in this environment |
> | P2-A variations, structure, approval | **VERIFIED** | axis + hypothesis enforced; approval records who, when and why |
> | P2-A guardrails and metering | **VERIFIED** | quantities clamped server-side; retries do not double-charge |
> | P2-A tenant isolation and RBAC | **VERIFIED** | 9 dedicated tests; every new write route in the authorization coverage guard |
> | P2-A publishing | **OUT OF SCOPE** | no publish call exists; terminal state is `READY_TO_PUBLISH` |
>
> Full detail: [`docs/AI_CREATIVE_ENGINE.md`](docs/AI_CREATIVE_ENGINE.md) and
> §0c of [`PRODUCTION_READINESS.md`](PRODUCTION_READINESS.md).

### P2-A-1 · Verify image and video generation against a real vendor — **S (mostly waiting on credentials)**
The provider adapters, storage write, retrieval, authorization and every failure
path are tested, but no vendor API key exists in this environment, so the round
trip to OpenAI Images and Replicate is **unverified**. Run
`apps/api/scripts/verify_real_media.py` (or `verify_p2a_e2e.py` for the full
pipeline) in an environment with `IMAGE_PROVIDER=openai` and
`VIDEO_PROVIDER=replicate` set; it reports the provider it actually used, exits 3
when a vendor round trip was skipped, and refuses to describe demo output as
real. Until then no claim of working vendor generation may be made.

The verification run found one real defect, now fixed: a video cancellation that
the provider **refused** was still being recorded as `CANCELLED` locally. The
provider's answer now decides the local state — a refusal leaves the job exactly
as it was and returns `502 MEDIA_CANCELLATION_FAILED`, because a local
`CANCELLED` on a generation that is still running and billing is the one state
nobody can recover from. Four regression tests cover the refusal, an unsupported
cancel, a completed job (provider not called) and a cross-tenant attempt.

### P2-A-2 · Decide the retention policy for generated media — **S**
Every generation writes to object storage and nothing prunes it. Concepts and
assets can be archived (soft, reversible, deliberately not a delete so an ad
cannot be orphaned) but archived bytes stay billable indefinitely. Needs a
documented retention window and a reaper job before this is sold by volume.

### P2-A-3 · Reconcile the two campaign-creation paths — **M**
`/campaign-builder` (P1) and `/ai-campaigns` (P2-A) both produce campaigns.
`Campaign.review_status` is the P2-A approval state and `Campaign.status` remains
the platform delivery state; the two paths write the same table with different
expectations. Fold the older builder into the generator, or state the division
explicitly in the UI, before a third path appears.

### P2-A-4 · Cost attribution per generation run — **M**
Usage is metered per event (`campaign_generation`, `strategy_generation`,
`copy_generation`, `image_generation`, `video_generation`,
`variation_generation`), which answers "how many" but not "what did this run
cost". Provider token and image counts are not captured per run, so margin per
campaign cannot be computed. Related to P3-12.

### P2-1 · Implement Stripe billing — **L**
Zero Stripe references exist repo-wide; `Subscription` (`app/models/ai_ops.py:73-80`) is an inert row created at registration and read by nothing. Add checkout, a signature-verified webhook, plan/seat gating, and a customer portal. **You cannot charge customers today.**

### P2-2 · Implement usage metering — **M**
No token counting, no cost attribution, no quotas, no credit ledger. Record per-organization AI calls, media generations, storage bytes, and seats; enforce plan limits; expose usage in Settings.

### P2-3 · Resolve multi-organization membership ambiguity — **M**
`app/core/deps.py:59-64` and `auth_service.me()` both use `.limit(1)` with no ordering, so a user in two organizations is bound to an arbitrary one with no way to switch. Add an explicit active-organization concept (claim or header) plus a workspace switcher — this is required for the agency use case.

### P2-4 · Complete Meta App Review and the Google Ads developer token — **L (mostly waiting)**
Live campaign execution is impossible without these. Approval takes **2–6 weeks**. Start now, in parallel with engineering.

### P2-5 · Implement live ads write adapters — **L**
`app/automation/execution.py:407-410` and `app/publishing/adapters.py:57-62` honestly refuse live writes. Once P2-4 clears, implement Meta and Google Ads campaign creation, budget updates, and pause/resume — **storing the real external ID** and never reporting success without a platform response.

### P2-6 · Add lead capture ingestion — **M**
Leads exist only if created in-app. Add a public, rate-limited, per-client form/webhook endpoint with spam protection and deduplication — this is the top of the funnel the product is built around.

### P2-7 · Add an audit-log viewer and close coverage gaps — **M**
`AuditLog` is written but has **no read API**, `ip_address` is never populated by any caller, and media generation, campaign builds, autopilot runs, and failed logins are not logged at all. Populate IP from the request, cover the missing actions, and expose a filterable admin view — essential for customer disputes over autonomous spend. Consider append-only enforcement or a hash chain so the app's own DB user cannot rewrite history.

### P2-11 · Make assistant conversations loadable — **S**
`AIConversation` rows are written (`app/api/v1/assistant.py:47-59`) but **nothing reads them back**, and each row holds a single turn. Add list/get endpoints and pass prior turns into the prompt so the assistant has memory.

### P2-8 · Add legal and compliance basics — **M**
Terms of Service, Privacy Policy, DPA, cookie consent, GDPR export/delete, and a documented retention policy. You are processing third-party marketing data on customers' behalf.

### P2-9 · Add transactional email — **S**
Required for verification, password reset, approval notifications, and failure alerts. `Notification` rows are written but never delivered anywhere.

### P2-10 · Backups and disaster recovery — **S**
Enable automated Postgres backups with point-in-time recovery, enable bucket versioning, and **perform a real restore drill**. An untested backup is not a backup.

---

## P3 — Post-launch improvements

| ID | Task | Effort |
|---|---|---|
| P3-1 | Postgres Row-Level Security so tenant isolation is enforced by the database, not code discipline | L |
| P3-2 | Add indexes on `(organization_id, created_at)` for `creative_assets`, `ai_actions`, `analytics_daily`, plus the unindexed FKs `ImageJob.client_id/campaign_id`, `VideoJob.client_id/campaign_id`, `CreativeAsset.campaign_id`, `Notification.user_id`, `AIAction.approved_by`; configure `pool_size`/`max_overflow`/`pool_recycle` (only `pool_pre_ping` is set today) | M |
| P3-3 | Frontend error boundaries, retry affordances, and optimistic UI | M |
| P3-4 | Expand tests: cross-tenant suite, worker tests, provider-failure simulations; target 70% coverage | L |
| P3-5 | Streaming (SSE) for AI strategy and assistant responses | M |
| P3-6 | Real-time job progress via WebSocket instead of polling | M |
| P3-7 | Additional media providers (Midjourney, Flux, Runway, Pika) behind the existing interfaces | L |
| P3-8 | Delete or wire up dead code: `app/storage/local.py` (unused, incompatible interface), `apps/web/src/components/PhasePlaceholder.tsx` (never imported), and `MonitoringAgent` (fully written, never invoked) | S |
| P3-13 | Fix `report_service.resolve_export_path` (`:257-260`) — it always assumes `.pdf` even when the reportlab-missing fallback wrote `.txt` (`:213-221`), producing a broken download | S |
| P3-14 | Reconcile the two competitor sources: the `/competitors` page uses the `Competitor` CRUD table while the client workspace tab reads the `client.competitors` string list (`clients/[id]/page.tsx:541-545`) | S |
| P3-15 | Add lead deduplication — `Lead.email` is indexed but not unique and `LeadRepository.create` has no duplicate check; wire `LeadActivity` into scoring (`known_activities` is always empty in `lead_service.py:28-39`) | M |
| P3-16 | Hold the access token in memory instead of `localStorage`, rehydrating on load via a silent cookie refresh. S4 removed the renewable half of the session from JavaScript's reach; this removes the remaining 60-minute window | M |
| P3-9 | Upgrade `bcrypt` (pinned `4.0.1` with passlib 1.7.4) and resolve `jose` `utcnow()` deprecation warnings | S |
| P3-10 | OpenAPI docs, API keys, and public API for customer integrations | L |
| P3-11 | Per-organization data export | M |
| P3-12 | Cost/margin dashboard: AI spend vs subscription revenue per tenant | M |

---

## Suggested sequencing

| Week | Focus | Tasks |
|---|---|---|
| **1** | Stop the bleeding | P0-1 → P0-10 |
| **2** | Durability | P1-1, P1-2, P1-3, P1-10 |
| **3** | Operability & security | P1-4 → P1-9, P1-11, P1-12 |
| **4** | Business & launch | P2-1, P2-2, P2-7, P2-9, P2-10, staging validation |
| **Parallel from day 1** | Platform approvals | P2-4 (2–6 week lead time) |
| **Post-launch** | Hardening | P2-3, P2-5, P2-6, P2-8, then P3 |
| **When credentials exist** | Prove the media vendors | P2-A-1, then P2-A-2 |

**Critical path to a payment-taking launch:** P0 (all) → P1-1, P1-2, P1-4, P1-5, P1-6, P1-8 → P2-1, P2-2, P2-10.

**Critical path to genuine live campaign execution:** the above, plus P2-4 → P2-5.
