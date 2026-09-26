# M9.2 — Technical SEO Analysis Engine

## Overview

The analysis engine transforms M9.1 crawler observations into deterministic, evidence-based technical SEO findings. It is **read-only** and never modifies external websites.

```
Crawler observations → Technical SEO analysis → Structured findings → Future AI recommendations
```

## Important limitation

**This engine analyzes observable technical conditions.** It does **not** directly determine Google indexing, ranking, traffic, backlinks, or search volume.

Findings use language such as:
- "Observed robots meta directive with noindex"
- "Title tag is missing"

Not:
- "Google has deindexed this page"
- "Your rankings are falling"

## Severity policy

| Severity | Meaning |
|----------|---------|
| **INFO** | Observation requiring context; not necessarily a problem |
| **LOW** | Optimization opportunity or minor structural concern |
| **MEDIUM** | Meaningful technical issue with clear evidence |
| **HIGH** | Potentially significant condition (e.g. HTTP 5xx) |

No synthetic "SEO score" is produced — summaries use factual counts.

## Rule categories

- **title** — missing, empty, duplicate, short/long (heuristic thresholds)
- **meta_description** — missing, empty, duplicate, short/long
- **headings** — H1 missing/empty/multiple, hierarchy observations
- **canonical** — missing, malformed, mismatch, cross-host
- **robots_meta** — observed directives (noindex, nofollow, etc.)
- **robots_txt** — unavailable, blocked URLs (distinct from HTTP failures)
- **sitemap** — not observed, inaccessible
- **links** — broken internal/external links
- **redirects** — chains, cross-host, suspected loops
- **url_quality** — duplicate normalized variants, trailing slash inconsistency
- **orphan** — pages without internal inbound links
- **crawl_depth** — pages deeper than configured heuristic
- **images** — missing alt (with decorative-image caveat)
- **structured_data** — JSON-LD block observations
- **hreflang** — malformed, duplicate, invalid codes
- **protocol** — HTTP URLs, mixed-protocol references
- **status** — 4xx/5xx, unexpected content types, empty responses

## Heuristic thresholds

Configured in `app/seo/analysis/thresholds.py` (not universal Google limits):

- Title: 10–70 characters (heuristic)
- Meta description: 50–160 characters (heuristic)
- Recommended max crawl depth: 4
- Redirect chain warning: 2+ hops

Thresholds are stored in finding evidence where relevant.

## API endpoints

All endpoints require authentication and are tenant-scoped.

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/v1/seo/crawls/{crawl_id}/findings` | List findings (filters: category, severity, status, rule_id, url) |
| GET | `/api/v1/seo/crawls/{crawl_id}/findings/compare?other_crawl_id=` | Compare findings between two crawls |
| GET | `/api/v1/seo/crawls/{crawl_id}/findings/{finding_id}` | Single finding |
| GET | `/api/v1/seo/crawls/{crawl_id}/summary` | Aggregate counts by severity/category |

Analysis runs automatically after crawl completion. Findings are persisted in `seo_findings`.

## Finding model

Each finding includes: `rule_id`, `category`, `severity`, `status`, `title`, `description`, `evidence`, `observed_value`, `expected_or_heuristic`, `recommendation`, `url`.

Deduplication uses `(crawl_id, dedupe_key)` unique constraint.

## Historical comparison

Compare two crawls to identify new, resolved, and persistent findings by stable `rule_id + url + dedupe_key`. Comparison does not imply causation.

## Local development

Analysis uses the same SQLite/Postgres database as crawls. Known local SQLite Alembic UUID drift may affect migration validation — the migration is valid for Postgres.
