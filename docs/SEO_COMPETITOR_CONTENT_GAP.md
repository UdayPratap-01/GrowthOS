# SEO Competitor & Content-Gap Analysis (M9.6)

## Objective

Deterministic competitor and content-gap analysis comparing:

- User Search Console topic clusters (M9.5)
- User keyword opportunities (M9.4)
- Observed competitor page content from bounded crawls

This milestone is **analysis only** — no publishing, metadata changes, AI recommendations, or autonomous actions.

## Data honesty

**Not available from competitor crawls:**

- Search rankings
- Competitor traffic, clicks, CTR
- Search volume
- Backlinks

Search Console data applies only to the connected user property.

If no competitor crawl data exists, APIs return `insufficient_competitor_data`.

## Architecture

```
M9.5 topic clusters + M9.4 opportunities
        ↓
M9.6 competitor configuration
        ↓
M9.1 crawler stack (SSRF-safe fetch + PageParser)
        ↓
Deterministic lexical matching
        ↓
Content-gap signals
```

## Competitor model

`seo_competitors` — organization-scoped:

- `root_url` (http/https only, SSRF validated)
- `domain` (normalized registrable domain)
- `display_name`, `status`

Max 10 active competitors per organization (configurable).

Separate from client-scoped marketing `competitors` CRUD — SEO competitors are org-scoped under `/api/v1/seo/competitors`.

## URL validation

Reuses M9.1 `validate_url_target()`:

- http/https only
- Blocks localhost, loopback, private/link-local IPs, metadata addresses
- DNS resolution checked before fetch
- Redirect targets re-validated on every hop

## Competitor crawling

`CompetitorSiteCrawler` reuses:

- `SafeFetcher`
- `PageParser`
- `robots.txt` handling
- `clamp_competitor_crawl_limits()`

Defaults: 25 pages, depth 2, 300s max duration. Job type: `seo.competitor_crawl`.

Stored in `seo_competitor_crawls` + `seo_competitor_pages`. Raw HTML is **not** stored — only extracted fields and JSON observations.

## Extraction

From each HTML page:

- title, meta description, H1, H2 headings
- deterministic token set
- topic label (H1 → title → URL fallback)
- word count, structured data presence flag

## Topic representation

**User site:** M9.5 `TopicCluster` + member queries (token sets).

**Competitor pages:** title + H1 + headings tokenized via M9.5 `tokenize_query()`.

## Matching algorithm (`competitor_gap_v1`)

Jaccard similarity over token sets (same as M9.5):

| Strength | Threshold |
|----------|-----------|
| strong_match | ≥ 0.45 |
| moderate_match | ≥ 0.25 |
| weak_match | ≥ 0.15 |

Max 5,000 page-topic comparisons per analysis run.

## Gap types (6)

1. `COMPETITOR_TOPIC_NO_USER_CLUSTER` — competitor content with no matching user topic
2. `COMPETITOR_PAGE_WEAK_USER_COVERAGE` — moderate match but limited user pages/impressions
3. `USER_OPPORTUNITY_NO_STRONG_PAGE` — keyword opportunity without strong associated page
4. `MULTI_COMPETITOR_LIMITED_USER` — multiple competitor pages, limited user coverage
5. `USER_TOPIC_COMPETITOR_DEPTH` — strong match with multiple competitor pages (depth signal)
6. `COMPETITOR_LEXICAL_NO_USER_TOPIC` — weak lexical overlap, no strong user topic

Terminology uses **content-gap signal** — never claims competitor content is "better" or ranks #1.

## Evidence model

Each gap stores structured `evidence`:

- `source_a`: search_console_topics
- `source_b`: competitor_crawl
- `similarity_method`: jaccard_token_overlap
- `data_honesty`: explicit note that rankings/traffic are not inferred

## Idempotency

Re-analysis deletes prior gaps for `(organization, sync, algorithm_version)` and creates a new `content_gap_analysis_runs` record. Dedupe key prevents duplicate gaps within a run.

## APIs

| Method | Path |
|--------|------|
| GET/POST | `/api/v1/seo/competitors` |
| GET/DELETE | `/api/v1/seo/competitors/{id}` |
| POST | `/api/v1/seo/competitors/{id}/crawl` |
| GET | `/api/v1/seo/competitors/{id}/crawl/{crawl_id}` |
| POST | `/api/v1/seo/content-gaps/analyze` |
| GET | `/api/v1/seo/content-gaps` |
| GET | `/api/v1/seo/content-gaps/summary` |
| GET | `/api/v1/seo/content-gaps/{id}` |

## Resource limits

- 10 competitors/org
- 25 pages/crawl default (100 hard cap)
- 6 competitor crawls/hour/org
- 12 content-gap analyses/hour/org
- 5,000 max comparisons/analysis

## Frontend

- `/seo/competitors` — add/remove competitors, start crawl
- `/seo/content-gaps` — run analysis, summary, filtered gap list

## Security

- Full M9.1 SSRF stack on competitor URLs and crawl redirects
- Tenant isolation on all endpoints
- Untrusted competitor HTML parsed without script execution
- Rate limiting on crawl and analyze endpoints
- Bounded pagination (max 500)

## Limitations

- Lexical matching only — synonyms may not align
- Requires prior topic clustering and competitor crawl data
- No external competitor rank/traffic APIs integrated
- Competitor crawl is read-only and bounded

## M9.7 handoff

Each `ContentGap` exposes structured fields for AI recommendations:

- `gap_type`, `topic_label`, `user_query`, `user_topic_id`
- `competitor_url`, `competitor_title`, `competitor_h1`
- `similarity`, `match_strength`, `evidence`, `explanation`
- source timestamps in evidence

M9.7 should consume these signals — not re-crawl or re-invent metrics.
