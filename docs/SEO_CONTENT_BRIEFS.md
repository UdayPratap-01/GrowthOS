# M9.8 — SEO Content Briefs

## Overview

M9.8 converts grounded **M9.7 SEO recommendations** and their underlying M9.1–M9.6 evidence into structured **content briefs** — planning artifacts for writers, not final articles.

Pipeline:

```
M9.7 SeoRecommendation
→ resolve evidence references (tenant-scoped)
→ bounded context builder
→ AI content brief generation
→ structured schema validation
→ grounding + internal-link validation
→ persisted SeoContentBrief
```

## Versions

| Constant | Value |
|----------|-------|
| Algorithm | `seo_content_brief_v1` |
| Prompt | `seo_content_brief_prompt_v1` |

## Recommendation dependency

Every brief **must** trace to a real M9.7 `SeoRecommendation` via `recommendation_id`.

Eligible recommendation types:

- `content_refresh`
- `keyword_targeting`
- `topic_expansion`
- `content_gap`
- `search_intent`
- `competitor_gap`

Not eligible (returns `400 unsupported_recommendation_type`):

- `technical_seo`
- `metadata_optimization`
- `page_structure`

## Evidence resolution

`build_brief_context()` loads the recommendation, resolves each `evidence_ref` from the database (organization-scoped), and builds:

- `resolved_evidence` — bounded evidence records
- `primary_keyword_candidates` — from keyword/topic/GSC evidence
- `internal_link_candidates` — from completed SEO crawl pages only
- keyword, URL, and topic indexes for grounding validation

The client cannot supply arbitrary evidence IDs — the server resolves everything from the recommendation.

## Primary keyword rule

Primary keyword must come from M9.4/M9.5/M9.7 evidence. If none exists:

```
primary_keyword = "unavailable"
```

The AI must not invent keywords.

## Search intent

Search intent is an **AI interpretation**, not a verified Google classification. Output includes `interpretation_note`.

## Competitor evidence

Competitor data is crawl/content observation only. Rankings, traffic, search volume, and backlinks remain unavailable.

## Internal-link validation

Every `internal_link_targets` entry must exist in `internal_link_candidates` from the organization's SEO crawl. Invented URLs are rejected.

## Structured AI output

Schema: `SeoContentBriefGenerated` with nested `SeoContentBriefItem`.

Includes outline sections, questions, entities, content/SEO requirements, and grounded `evidence_refs`.

## Provider behavior

| Condition | Response |
|-----------|----------|
| Missing AI config | `503 ai_provider_unavailable` |
| Generation error | `502 ai_generation_failed` |
| Grounding failure | `422 grounding_validation_failed` |
| No evidence | `400 insufficient_evidence` |
| Unsupported rec type | `400 unsupported_recommendation_type` |
| Context too large | `400 context_too_large` |

No silent fallback from real AI to mock in production.

## Idempotency

`generation_key = sha256(org_id | recommendation_id | context_hash | prompt_version | algorithm_version)`

Regenerating with the same key replaces the prior brief.

## API

Base path: `/api/v1/seo/content-briefs`

| Method | Path | Description |
|--------|------|-------------|
| POST | `/generate` | Generate brief from recommendation_id |
| GET | `/` | List with filters |
| GET | `/{id}` | Brief detail |
| POST | `/{id}/archive` | Archive brief |

Rate limits: `seo_content_brief_generate_limit`, `ai_limit`, AI quota.

## Frontend

Route: `/seo/content-briefs`

Select a recommendation, generate a brief, review outline and evidence with clear separation between **source evidence** and **AI brief guidance**.

## Scope boundaries

M9.8 generates content briefs only.

It does **NOT**:

- Generate final articles
- Publish content
- Modify website content or metadata
- Deploy schema or internal links automatically

## M9.9 handoff

`SeoContentBrief` records expose structured fields for article generation:

- brief ID, recommendation ID, brief type
- primary/secondary keywords, target topic, search intent
- target URL, content goal, audience, content type, angle
- outline, questions, entities, internal links
- content/SEO requirements, evidence refs, limitations

## Known limitations

- Requires existing M9.7 recommendations with resolved evidence
- Target audience may be `unavailable` without client context
- Brief quality depends on evidence breadth and AI provider availability
