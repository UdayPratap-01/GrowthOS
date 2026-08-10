# AI Creative & Campaign Engine (P2-A)

The engine turns a client's **stored** business context into a complete campaign
package: strategy, brief, ad copy, creative concepts, real image and video
assets, variations, and a campaign → ad set → ad structure, all held behind an
explicit human approval.

**It cannot spend advertising money.** There is no publish call, no budget
mutation against a live campaign, and no `PUBLISHED` state anywhere in the
lifecycle. The terminal state is `READY_TO_PUBLISH`, which means "a human has
signed off" and nothing more. Publishing is a later phase.

---

## Contents

1. [Architecture](#1-architecture)
2. [Client context and the no-fabrication rule](#2-client-context-and-the-no-fabrication-rule)
3. [AI agents](#3-ai-agents)
4. [Orchestration and the run lifecycle](#4-orchestration-and-the-run-lifecycle)
5. [Provider abstraction](#5-provider-abstraction)
6. [Image generation](#6-image-generation)
7. [Video generation](#7-video-generation)
8. [Storage](#8-storage)
9. [Background jobs](#9-background-jobs)
10. [Creative variations](#10-creative-variations)
11. [Creative formats and platform registry](#11-creative-formats-and-platform-registry)
12. [Campaign structure and approval](#12-campaign-structure-and-approval)
13. [Usage metering and cost guardrails](#13-usage-metering-and-cost-guardrails)
14. [Security model](#14-security-model)
15. [Database](#15-database)
16. [API reference](#16-api-reference)
17. [Frontend](#17-frontend)
18. [Configuration](#18-configuration)
19. [Setup](#19-setup)
20. [Verification](#20-verification)
21. [Known limitations](#21-known-limitations)

---

## 1. Architecture

```
Browser  /ai-campaigns/new
   │  POST /api/v1/campaign-generation/generate            (returns 202 + run id)
   ▼
CampaignGenerationService.start()
   │  validates client ownership, platform, objective
   │  clamps quantities against GenerationLimits
   │  checks billing quota, records CampaignGenerationRun
   │  enqueues campaign.generate  ──────────────┐
   ▼                                            │
202 Accepted  ← the request ends here           │  DB-backed job queue
                                                │  (organization-scoped claim)
Browser polls GET /runs/{id}                    ▼
   ▲                              app.worker.Worker → handle_generate_campaign
   │                                            │
   │                              CampaignGenerationService.execute()
   │                                 1 CampaignContextBuilder
   │                                 2 CampaignStrategyAgent
   │                                 3 CreativeBriefAgent
   │                                 4 CopyAgent
   │                                 5 CreativeConceptAgent
   │                                 6 enqueue media  ──► media.generate_image
   │                                 7 VariationAgent               media.generate_video
   │                                 8 CampaignBuilderAgent                │
   │                                            │                         ▼
   └──── stages, with real counts ──────────────┘         ImageGenerationProvider
                                                          VideoGenerationProvider
                                                                    │
                                                          object storage (S3/local)
                                                                    │
                                                          CreativeAsset row
```

Nothing in this diagram is new infrastructure. The queue, worker, storage
abstraction, provider factory, AI orchestrator, usage meter, rate limiter and
RBAC layer are all the existing P0/P1 components; P2-A adds agents, a service, a
registry and four tables.

### Module map

| Path | Role |
|---|---|
| `app/campaigns/registry.py` | Platform, objective, aspect-ratio and limit specs |
| `app/campaigns/context.py` | `CampaignContextBuilder` — assembles real client context |
| `app/campaigns/errors.py` | Structured campaign errors |
| `app/ai/agents/*.py` | The eight agents |
| `app/schemas/campaign_generation.py` | Typed agent outputs and API contracts |
| `app/services/campaign_generation_service.py` | Orchestration, persistence, approval |
| `app/services/media_generation_service.py` | Media jobs, storage, cancellation |
| `app/api/v1/campaign_generation.py` | Endpoints |
| `app/jobs/handlers.py` | `handle_generate_campaign`, `handle_reconcile_campaign_run` |
| `app/models/creative.py` | Run, brief, concept, variation |

---

## 2. Client context and the no-fabrication rule

`CampaignContextBuilder.build()` extends the existing `ClientService.build_client_context`
with a 90-day performance window drawn from real rows: `AnalyticsCampaign` for
campaign performance, `SocialPost` metrics for content performance, `Lead` for
funnel volume, and `Strategy` for prior strategies.

The important part is what it does when the data is not there. Two separate
fields are produced:

- `available_metrics` — numbers that exist, with the field they came from.
- `data_limitations` — plain statements of what is missing, e.g.
  `"No historical Meta campaign data available."`

An agent receives both. Performance metrics that would be a claim — CTR, CPL,
ROAS, conversion rate, revenue, website traffic, competitor metrics, audience
behaviour — **do not exist as fields on any agent output schema**. A field that
does not exist cannot be invented, which is a stronger guarantee than a prompt
instruction not to invent one.

Where a recommendation does rest on a metric, `CampaignStrategy.evidence` carries
`claim`, `source` and `value`, so a reviewer can trace a finding back to the row
it came from. An unsourced statement is a judgement and the UI renders it
differently from a sourced one.

If the organization is in demo mode, `history_is_demo` is set and the limitation
list says so explicitly. Demo history never silently becomes evidence.

---

## 3. AI agents

Eight agents, each with a typed input, a typed output validated by
`BaseAgent.run`, and no knowledge of which provider is behind it. A malformed or
hallucinated response fails validation loudly rather than half-populating a
campaign.

| Agent | Output schema | Responsibility |
|---|---|---|
| `CampaignStrategyAgent` | `CampaignStrategy` | 13 strategy sections, success metrics, risks, evidence, limitations |
| `CreativeBriefAgent` | `CampaignBriefDraft` | Offer, audience, pain points, value proposition, messaging angle, constraints |
| `CopyAgent` | `CopyConceptPack` | N genuinely different concepts: angle, hook, primary text, headline, CTA, hypothesis |
| `CreativeConceptAgent` | `CreativeConceptPack` | Visual direction plus the image and video prompts and negative constraints |
| `ImageCreativeAgent` | — | Renders a stored prompt through the image provider |
| `VideoCreativeAgent` | — | Renders a stored prompt through the video provider |
| `VariationAgent` | `VariationPack` | One-axis variations with a stated hypothesis |
| `CampaignBuilderAgent` | `CampaignBlueprint` | Ad sets with budget *shares*, and ads bound to concepts |

Two design decisions worth knowing:

**The concept agent produces prompts; the media agents render them.** The prompt
is written once, at generation time, and stored on the concept. Regenerating an
image re-renders the stored prompt rather than asking the LLM for a new one — so
a regeneration is a retry of the same creative idea, not a different one, and it
costs one image instead of one LLM call plus one image.

**The builder agent returns budget shares, not amounts.** `budget_share` is
0–1 and the server does the arithmetic. A model that returned amounts could
inflate the total past what the user asked for; a share cannot.

---

## 4. Orchestration and the run lifecycle

`CampaignGenerationRun` is the unit of progress. Its `stages` list is written by
the worker as it goes, and each stage carries `completed`/`total` counts of real
finished work:

```
Strategy          COMPLETED
Copy              COMPLETED
Creative concepts COMPLETED
Images            RUNNING          2/3
Videos            NOT_CONFIGURED
Variations        COMPLETED        2/2
Campaign          COMPLETED
```

Run statuses: `QUEUED → GENERATING → READY_FOR_REVIEW`, or `FAILED`.
Stage statuses: `PENDING`, `RUNNING`, `COMPLETED`, `FAILED`, `NOT_CONFIGURED`,
`SKIPPED`.

The frontend polls the run and renders those rows directly. There is no
percentage and no animated bar, because the total is not knowable in advance — a
media stage can turn out to be `NOT_CONFIGURED` — and a bar that reached 90% and
stopped would be a lie told smoothly.

`reconcile()` recounts media jobs and promotes the run to `READY_FOR_REVIEW` only
when every media job is terminal and the structure exists. It reschedules itself
via `campaign.reconcile` while work remains, so progress advances even if no
browser is watching.

`terminal` is returned by the server rather than inferred client-side, so adding
a state later does not make an old client exit its poll loop early.

---

## 5. Provider abstraction

```python
class ImageGenerationProvider(ABC):
    name: str
    def configured(self) -> bool: ...
    async def generate_image(self, request) -> GenerationResult: ...
    async def get_status(self, provider_job_id) -> GenerationResult: ...
    async def cancel(self, provider_job_id) -> GenerationResult:  # default: unsupported
        ...

class VideoGenerationProvider(ABC):
    name: str
    def configured(self) -> bool: ...
    async def create_video(self, request) -> GenerationResult: ...
    async def get_status(self, provider_job_id) -> GenerationResult: ...
    async def cancel(self, provider_job_id) -> GenerationResult: ...
```

`cancel()` has a base implementation returning `cancel_unsupported` rather than
being abstract, because most image APIs are synchronous and have nothing to
cancel. A provider that *can* cancel overrides it — `ReplicateVideoProvider`
calls Replicate's cancel endpoint, which is what stops a long generation from
continuing to cost money rather than merely hiding it from the library.

### Cancellation semantics

For a video job with a provider job id, the provider's answer decides the local
state:

| Provider answer | Local result |
| --- | --- |
| Cancelled | job becomes `CANCELLED` |
| Refused (e.g. Replicate 409) or no cancel support | job is **unchanged**; `502 MEDIA_CANCELLATION_FAILED` |
| — (job already `COMPLETED`/`FAILED`/`CANCELLED`) | provider is not called; current status returned |

A refused cancellation must not be recorded as `CANCELLED`: that would leave a
generation still running and billing at the vendor with nothing in the product
watching it. The refusal is logged as a `CANCEL_FAILED` media event with the
provider's own message, while the response carries only the mapped error code
(`provider_error_code`) — a provider response body can echo request content, so
it is never returned to the caller. The caller may retry; each request makes at
most one provider call.

Selection goes through the existing factory (`get_image_provider`,
`get_video_provider`) driven by `IMAGE_PROVIDER` / `VIDEO_PROVIDER`. No agent,
service or endpoint names a vendor. Provider keys live in backend settings and
are never sent to the frontend — the frontend receives only the provider *name*
and a `configured` boolean.

Shipped adapters: `openai` (images), `demo` (images), `replicate` (videos),
`none`.

---

## 6. Image generation

```
POST /creative/images/generate  or  campaign generation
   → ImageJob row (QUEUED)
   → media.generate_image on the queue
   → worker claims it
   → ImageCreativeAgent → ImageGenerationProvider.generate_image()
   → real bytes returned
   → bytes validated as an image
   → uploaded to object storage under the tenant prefix
   → read back to confirm the object exists
   → CreativeAsset row created, status COMPLETED
```

A job reaches `COMPLETED` only after every one of those steps. Specifically it is
**not** completed when the provider returns a URL but no bytes, when the bytes do
not begin with a known image signature, when the storage upload raises, or when
storage reports success but a read-back returns nothing. Each of those is a test
in `tests/test_campaign_media_pipeline.py`.

`aspect_ratio` is resolved from the platform registry, not hard-coded in the
agent.

With no image provider configured the job is never created: the stage reports
`NOT_CONFIGURED`, no asset row exists, no URL is offered, and the rest of the
campaign still generates. Production never falls back to the demo provider.

---

## 7. Video generation

Video is asynchronous at the vendor, so the pipeline has an extra hop:

```
create_video() → provider job id → VideoJob (SUBMITTED)
   → media.poll_video re-enqueued with backoff
   → get_status() → still processing → re-enqueue
   → get_status() → succeeded, with an output URL
   → download the bytes
   → validate, upload, read back
   → CreativeAsset row, status COMPLETED
```

States: `QUEUED`, `GENERATING`, `PROCESSING`, `COMPLETED`, `FAILED`,
`CANCELLED`, `NOT_CONFIGURED`.

No HTTP request ever waits for a video. `VIDEO_JOB_TIMEOUT_SECONDS` fails a job
whose provider stopped responding rather than leaving it `PROCESSING` forever.
An MP4 is never fabricated; the demo video provider deliberately declines to
produce one and fails honestly.

---

## 8. Storage

Generated bytes go through the existing `ObjectStorage` abstraction (`local` for
development, `s3` for anything real — AWS, R2, MinIO, Wasabi, Spaces).

Keys are prefixed per organization, and `key_belongs_to_organization()` is
checked again when bytes are served. The list endpoint is already tenant-scoped,
so this second check exists specifically so that a forged or corrupted
`storage_key` on a row cannot be used to read another tenant's object.

Bytes are served by `GET /creative/media/{asset_id}`, authenticated and
tenant-checked, never by a public link. `?download=true` adds a
`Content-Disposition` header with a filename sanitised from the asset name — the
name originates in model-written text, so it is filtered to safe characters.

A storage outage returns 503, not 404: telling a user their asset is gone when it
is merely unreachable is a different and worse failure.

---

## 9. Background jobs

Job types added: `campaign.generate`, `campaign.reconcile`. Existing:
`media.generate_image`, `media.generate_video`, `media.poll_video`.

All use the P0-9 DB-backed queue — atomic compare-and-swap claiming, leases with
recovery for dead workers, retries with backoff. No second queue implementation
was introduced.

Tenancy: every organization-facing payload carries `organization_id`, the claim
predicate applies it, and handlers assert ownership again before touching a row.
The worker process itself runs unscoped because it is trusted infrastructure and
must drain every tenant's work; anything reachable from an HTTP request is
scoped. `tests/test_campaign_media_pipeline.py::test_a_job_cannot_be_run_for_another_tenant`
covers the boundary.

Idempotency: a repeated `idempotency_key` returns the existing run or job instead
of starting a second one, so a double-click or a client retry loop cannot
double-charge.

---

## 10. Creative variations

A variation changes exactly one declared axis: `hook`, `visual`, `offer`, `cta`,
`tone`, `composition`, `format`, `audience_angle`. Each records
`parent_concept_id`, `axis`, `hypothesis`, `creative_type` and `status`.

The axis is a constrained `Literal`, not free text, and the hypothesis is
required. That is what stops "variation" from degrading into an unlabelled
reword: a variation has to say which lever it pulled and what it is testing.

```
Concept A   "Stop overpaying for a roof you can repair"
Variation B  axis=hook           "Still patching the same leak every spring?"
Variation C  axis=audience_angle "What landlords check before storm season"
```

Variations can optionally generate their own media (`generate_media: true`),
which is off by default so a click on "Create variations" cannot silently start a
batch of image renders.

---

## 11. Creative formats and platform registry

`app/campaigns/registry.py` holds the configuration that platform-specific
behaviour is read from:

- `PlatformSpec` — supported aspect ratios, defaults for image and video,
  placements, video support, headline and primary-text character caps.
- `ObjectiveSpec` — label, description, optimization goal, success metrics.
- `AspectRatioSpec` — `1:1`, `4:5`, `9:16`, `16:9` with pixel dimensions,
  orientation and typical usage.
- `GenerationLimits` — the ceilings, resolved from settings.

Agents receive resolved specs as data. Adding a platform is a registry entry, not
a prompt edit or a branch in an agent, which is the point: the same creative
system should carry a platform that does not exist yet.

Connection state is separate from support. `PlatformAvailability.connected`
reflects this organization's actual integrations, and
`publishing_supported` is `false` for every platform throughout P2-A.

---

## 12. Campaign structure and approval

Existing models are extended rather than duplicated:

- `Campaign` gains `review_status`, `objective`, budget fields, `audience`,
  `generated_by_ai`, `brief_id`, and the approval columns.
- `AdSet` gains `audience`, `optimization`, `placements`.
- `Ad` gains `concept_id`, `variation_id`, `creative_asset_id`, `headline`,
  `primary_text`, `cta`, `destination`.

**`review_status` is deliberately separate from `status`.** `status` is what an ad
platform is doing with a campaign (active, paused); `review_status` is what a
human agreed to inside GrowthOS. Collapsing them would lose the approval record
the moment a campaign went live, and would make "approved" indistinguishable from
"delivering".

```
DRAFT → GENERATING → READY_FOR_REVIEW → READY_TO_PUBLISH
                            └─────────→ REJECTED
```

Approval records `approved_by`, `approved_at`, `approval_comment`; rejection
records `rejected_by`, `rejected_at`, `rejection_reason`. Both are audited. A
second approval is a `409`, not a silent no-op.

Approval requires `Permission.action_approve` (admin and owner). A member can
generate a campaign but cannot sign off their own work — separation of duties on
a package that authorises future spend.

There is no `PUBLISHED` state. `campaigns.external_id` is the only evidence a
campaign exists on a platform, nothing in P2-A writes it, and the UI cannot show
a campaign as published without it.

---

## 13. Usage metering and cost guardrails

Metered through the existing P1-8 meter, per organization:
`campaign_generation`, `strategy_generation`, `copy_generation`,
`image_generation`, `video_generation`, `variation_generation`.

Retries do not double-charge: metering is keyed on the logical operation, and a
repeated idempotency key returns the original record without billing again.

Before any expensive call the service validates authentication, organization,
client ownership, permission, provider availability, plan quota and requested
quantity — in that order, so nothing is spent before authorization is settled.

Quantities are **clamped**, not rejected:

| Setting | Default |
|---|---|
| `MAX_CONCEPTS_PER_GENERATION` | 5 |
| `MAX_IMAGES_PER_GENERATION` | 8 |
| `MAX_VIDEOS_PER_GENERATION` | 4 |
| `MAX_VARIATIONS_PER_GENERATION` | 12 |
| `CAMPAIGN_GENERATION_RATE_LIMIT_PER_HOUR` | 20 |

Clamping rather than rejecting means a mistyped `image_quantity: 500` produces a
usable package at the ceiling instead of failing after the strategy has already
been paid for. The frontend also shows the ceilings, but the server is the
control — `test_quantities_are_clamped_server_side` asserts a raw API request is
clamped with no frontend involved.

---

## 14. Security model

Every endpoint enforces authentication, organization scope, client ownership,
RBAC and asset ownership.

| Route group | Permission |
|---|---|
| `GET /options`, `/runs`, `/campaigns`, `/package` | authenticated (viewer can read) |
| `POST /generate`, `/variations`, `/regenerate`, `/archive` | `content_write` (member+) |
| `POST /approve`, `/reject` | `action_approve` (admin+) |

Cross-tenant access returns **404, not 403**. A "forbidden" reply would confirm
the record exists, which is itself a leak. `tests/test_campaign_generation_security.py`
drives every route with another tenant's real ids and asserts 404 on all of them,
then re-reads the victim's campaign to prove its state was untouched.

Filtering by another tenant's id is not a lookup either: `?campaign_id=<theirs>`
returns `[]`.

New write routes are registered in the `test_authorization.py` coverage guard,
which fails if a financial or platform-writing route ships without a permission
dependency. Frontend button visibility is not a control and is never relied on.

---

## 15. Database

Four new tables, migration `f94b6d3a8c21`:

| Table | Purpose |
|---|---|
| `campaign_generation_runs` | Run status, stages, request, result, limitations |
| `campaign_briefs` | The structured brief plus the strategy document |
| `creative_concepts` | Copy, visual direction, prompts, negative constraints |
| `creative_variations` | Axis, hypothesis, overridden fields |

All carry `organization_id` and `client_id`, indexed, with cascading foreign
keys and a composite `(organization_id, campaign_id)` index on concepts.

Statuses are stored as strings rather than native PostgreSQL enums, matching the
billing migration: a lifecycle gains states, and adding one to a native enum
requires DDL on a live table.

JSON collection columns are `NOT NULL` with an empty default, matching the models
which type them as `Mapped[dict]` / `Mapped[list]`. Migration `a17c5e8b4d90`
applies the same correction to six pre-existing columns in the billing and usage
tables, so `alembic check` is now clean — see `docs/MIGRATIONS.md`.

`create_all` is never used in production; the guard is unchanged.

---

## 16. API reference

All paths are under `/api/v1`.

| Method | Path | Notes |
|---|---|---|
| `GET` | `/campaign-generation/options` | Platforms, objectives, formats, limits, provider status |
| `POST` | `/campaign-generation/generate` | **202** with the run to poll |
| `GET` | `/campaign-generation/runs` | Recent runs, filterable by client |
| `GET` | `/campaign-generation/runs/{run_id}` | Run status, reconciled on read |
| `GET` | `/campaign-generation/campaigns` | Generated campaigns, filterable by review status |
| `GET` | `/campaign-generation/campaigns/{id}/package` | The complete reviewable package |
| `POST` | `/campaign-generation/campaigns/{id}/approve` | admin+ |
| `POST` | `/campaign-generation/campaigns/{id}/reject` | admin+, reason required |
| `POST` | `/campaign-generation/concepts/{id}/variations` | |
| `POST` | `/campaign-generation/concepts/{id}/regenerate` | Re-renders stored prompts |
| `POST` | `/campaign-generation/concepts/{id}/archive` | `?archived=true|false` |
| `GET` | `/creative/assets` | Library: client, campaign, concept, variation, type, status, archived |
| `POST` | `/creative/assets/{id}/archive` | Soft, reversible |
| `GET` | `/creative/media/{id}` | Bytes; `?download=true` for an attachment |
| `POST` | `/creative/images/jobs/{id}/cancel` | |
| `POST` | `/creative/videos/jobs/{id}/cancel` | Asks the provider to stop; a refusal returns 502 and leaves the job as it was |

### Errors

Structured through the existing error system. API keys, provider secrets and
stack traces are never exposed.

| Code | HTTP | Meaning |
|---|---|---|
| `INVALID_CAMPAIGN_REQUEST` | 400 | Unknown platform or objective, bad input |
| `USAGE_LIMIT_REACHED` | 402 | Plan quota exhausted |
| `PERMISSION_DENIED` | 403 | Role is insufficient |
| `INVALID_CAMPAIGN_STATE` | 409 | e.g. approving an already-decided campaign |
| `AI_GENERATION_FAILED` | 502 | The AI provider did not return a valid response |
| `CAMPAIGN_GENERATION_FAILED` | 502 | The pipeline failed; the run records why |
| `MEDIA_CANCELLATION_FAILED` | 502 | The provider refused to cancel; the job keeps its real state |
| `MEDIA_PROVIDER_NOT_CONFIGURED` | 503 | No provider for the requested media |
| `STORAGE_UNAVAILABLE` | 503 | Storage outage, distinct from a missing file |

---

## 17. Frontend

| Route | Purpose |
|---|---|
| `/ai-campaigns` | Generated campaigns and in-flight runs |
| `/ai-campaigns/new` | The generator form |
| `/ai-campaigns/[campaignId]` | Preview, review and approval |
| `/creative-library` | Extended with campaign/status/archived filters, download, archive |

Components live in `apps/web/src/components/campaign/`:
`GenerationProgress`, `StrategyPanel`, `ConceptCard`, `AssetTile`,
`ApprovalPanel`, `DataLimitations`.

Behaviour worth preserving:

- **Media status is always visible** — `QUEUED`, `GENERATING`, `PROCESSING`,
  `COMPLETED`, `FAILED`, `NOT_CONFIGURED` each render distinctly, and a tile with
  no URL shows its status rather than a broken image.
- **REAL and DEMO are stated, not implied.** Demo assets are labelled in the
  library, on the tile and on the preview.
- **The form is driven by `/options`.** It cannot offer a platform the backend
  does not support, a format the platform does not accept, or a quantity above
  the server ceiling. Video inputs disable themselves when no provider exists.
- **Prompts are shown** on request, so a reviewer can judge whether a concept was
  grounded in this client's offer or is generic filler.
- **Approval states what it does and does not do** — nothing was sent to an ad
  platform, and the panel says so on an approved campaign.

---

## 18. Configuration

P2-A introduces **no new provider credentials**. It generates through the
existing `AI_PROVIDER`, `IMAGE_PROVIDER` and `VIDEO_PROVIDER`.

| Variable | Default | Purpose |
|---|---|---|
| `MAX_CONCEPTS_PER_GENERATION` | 5 | Ceiling per run |
| `MAX_IMAGES_PER_GENERATION` | 8 | Ceiling per run |
| `MAX_VIDEOS_PER_GENERATION` | 4 | Ceiling per run |
| `MAX_VARIATIONS_PER_GENERATION` | 12 | Ceiling per run |
| `CAMPAIGN_GENERATION_RATE_LIMIT_PER_HOUR` | 20 | Runs per organization per hour |

Relevant existing variables: `AI_PROVIDER`, `OPENAI_API_KEY`,
`ANTHROPIC_API_KEY`, `IMAGE_PROVIDER`, `IMAGE_API_KEY`, `IMAGE_MODEL`,
`VIDEO_PROVIDER`, `VIDEO_API_KEY`, `VIDEO_MODEL`, `VIDEO_JOB_TIMEOUT_SECONDS`,
`STORAGE_BACKEND` and the `S3_*` group, `REDIS_URL`, `INLINE_JOB_EXECUTION`,
`MEDIA_RATE_LIMIT_PER_HOUR`, `AI_RATE_LIMIT_PER_MINUTE`.

No provider secret is exposed to the frontend. `/campaign-generation/options`
returns the provider *name* and a `configured` boolean, nothing more.

---

## 19. Setup

### Development

```bash
# apps/api/.env
AI_PROVIDER=mock            # pre-written text; refused in production
IMAGE_PROVIDER=demo         # a real PNG, labelled DEMO
VIDEO_PROVIDER=none         # reports NOT_CONFIGURED
STORAGE_BACKEND=local
DEMO_MODE=true
INLINE_JOB_EXECUTION=true   # no separate worker needed
```

```bash
cd apps/api && alembic upgrade head && uvicorn app.main:app --reload
cd apps/web && npm run dev
```

### Real generation

```bash
AI_PROVIDER=openai
OPENAI_API_KEY=sk-...
IMAGE_PROVIDER=openai
IMAGE_MODEL=dall-e-3
VIDEO_PROVIDER=replicate
VIDEO_API_KEY=r8_...
VIDEO_MODEL=owner/name        # or a version hash
STORAGE_BACKEND=s3
S3_BUCKET=growthos-media
REDIS_URL=redis://...
INLINE_JOB_EXECUTION=false
DEMO_MODE=false
```

Run the worker as its own process — inline execution is refused in production:

```bash
python -m app.worker
```

---

## 20. Verification

```bash
# Backend suite
cd apps/api && python -m pytest -q                      # 647 passed

# Fresh PostgreSQL 16
alembic upgrade head && alembic check                   # no drift

# End to end, through the queue and the real worker
python scripts/verify_p2a_e2e.py

# The vendor seam: adapters, media chain, cancellation, REAL/DEMO/NOT_CONFIGURED
python scripts/verify_real_media.py                     # exit 3 = no vendor key
```

`verify_p2a_e2e.py` drives HTTP requests, lets `app.worker.Worker` drain the
queue out of band, then reads the generated bytes back through the authenticated
API and checks them against image magic numbers. It forces
`INLINE_JOB_EXECUTION=false` so it cannot pass without a worker, and it reports
the provider it actually used as `REAL`, `DEMO` or `NOT_CONFIGURED` — a run
against the demo provider is labelled as proving the pipeline and nothing about a
vendor's output.

Results in this environment (no vendor credentials present):

| Configuration | Result |
|---|---|
| PG16, `IMAGE_PROVIDER=demo`, `VIDEO_PROVIDER=none` | 57/57 checks passed |
| PG16, both providers unset | 36/36 checks passed, everything `NOT_CONFIGURED` |
| `verify_real_media.py`, no vendor key | 65/65 checks passed, exit 3 (vendor round trip skipped) |

`verify_real_media.py` covers what a unit test cannot: which provider the factory
actually resolves, the exact URL and auth scheme each adapter puts on the wire,
and the cancellation contract. Without credentials it substitutes a local
stand-in for the vendor HTTP API and says so in its exit code, so a passing run
is never mistaken for a working vendor.

Test files — 39 tests across three files, plus parametrized cases added to the
authorization coverage guard, taking the suite from 581 to 647:

| File | Tests | Covers |
|---|---|---|
| `tests/test_campaign_generation.py` | 14 | Options, generation end to end, metering, idempotency, clamping, variations, regeneration, approval, rejection, listing, archiving |
| `tests/test_campaign_media_pipeline.py` | 16 | Provider failure, non-image bytes, storage failure, unreadable upload, duplicate delivery, cross-tenant jobs, async video, cancellation incl. a refused provider cancellation |
| `tests/test_campaign_generation_security.py` | 9 | Cross-tenant reads and writes, unauthenticated access, viewer and member restrictions, admin approval |

---

## 21. Known limitations

1. **Real vendor generation is unverified here.** No image or video vendor key
   exists in this environment. The adapters, storage path, job pipeline,
   authorization and every failure branch are tested, and `verify_real_media.py`
   additionally proves the request each adapter builds and the cancellation
   contract, but the round trip to OpenAI Images and Replicate has not been
   executed. Tracked as P2-A-1.
2. **A generation the provider will not cancel keeps running.** When the vendor
   refuses cancellation the job is left in its real state and the caller gets
   `MEDIA_CANCELLATION_FAILED`; the render finishes and is billed. That is
   deliberate — the alternative is a database that says `CANCELLED` while the
   vendor meter runs — but it means "cancel" is a request, not a guarantee.
3. **Publishing is not implemented.** By design. `READY_TO_PUBLISH` means a human
   approved a proposal, not that anything reached an ad platform.
4. **No media retention policy.** Generated bytes accumulate; archiving is
   deliberately soft so an ad is never orphaned, but archived objects stay
   billable. Tracked as P2-A-2.
5. **Two campaign-creation paths coexist.** `/campaign-builder` (P1) and
   `/ai-campaigns` (P2-A) both write `campaigns`. Tracked as P2-A-3.
6. **Cost per run is not attributed.** Usage counts events, not provider tokens
   or per-image cost, so margin per campaign cannot be computed. Tracked as
   P2-A-4.
7. **Progress is polled, not pushed.** The client polls every 2–4 seconds; a
   WebSocket would be cheaper at scale (P3-6).
8. **`aspect_ratio` is advisory for some providers.** Not every vendor honours an
   exact ratio; the stored `width`/`height` reflect what was actually returned,
   not what was requested.
9. **Concept quality depends on stored client context.** A client record with no
   products, audience or brand voice yields a thin strategy — correctly, with the
   gaps listed in `data_limitations` rather than filled in with invention.
