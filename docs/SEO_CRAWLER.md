# M9.1 — Full-Site SEO Crawler

Read-only crawler foundation for GrowthOS "Done-for-You SEO". The crawler never mutates external websites.

## Architecture

```
POST /api/v1/seo/crawls
  → SeoCrawl row (QUEUED)
  → BackgroundJob (seo.crawl)
  → Worker / inline processor
  → SiteCrawler (BFS, robots-aware, SSRF-safe)
  → SeoCrawlPage rows (observations)
```

## Crawl states

| State | Meaning |
|-------|---------|
| `queued` | Persisted; job waiting |
| `running` | Worker executing crawl |
| `completed` | Finished within limits |
| `failed` | Root URL invalid / SSRF / fatal error |
| `cancelled` | User requested cancel |

## Limits (server-enforced)

User config is clamped to hard maximums in `app/seo/limits.py`:

- Default max pages: 50 (hard max 500)
- Default max depth: 3 (hard max 10)
- Request timeout, response bytes, queue size, total duration
- Max 3 active crawls per organization

## robots.txt

- Fetched once per crawl from site root
- `Disallow` / `Allow` evaluated for `User-agent: *`
- `Crawl-delay` respected via request delay when present
- `Sitemap` directives used for seed URLs

## SSRF protection

Before every request (including each redirect hop):

1. Resolve hostname to IP addresses
2. Reject private, loopback, link-local, reserved, and metadata IPs
3. Reject blocked hostnames (`localhost`, `metadata.google.internal`, …)

## URL normalization policy

Documented in `app/seo/normalize.py`:

- Lowercase scheme/host, strip fragments, strip default ports
- Remove common tracking query params (`utm_*`, `gclid`, `fbclid`)
- Preserve other query params (sorted for dedupe)
- Remove trailing slashes on non-root paths

## API endpoints

| Method | Path | Description |
|--------|------|-------------|
| POST | `/api/v1/seo/crawls` | Queue crawl (202) |
| GET | `/api/v1/seo/crawls/{id}` | Crawl status |
| GET | `/api/v1/seo/crawls/{id}/pages` | Page observations |
| POST | `/api/v1/seo/crawls/{id}/cancel` | Cancel crawl |
| POST | `/api/v1/seo/audit` | Single-page audit (sync) |

## Data provenance

All crawl observations are tagged `data_source: http_crawl`. They are **not** Search Console index data and must not be presented as rankings or traffic.

## Local development

- Requires worker or `INLINE_JOB_EXECUTION` for async crawls to process
- Uses same job queue as integrations/reports
- No production domain required for crawling public URLs

## Production considerations

- Rate limited per organization (`seo_crawl` policy)
- Outbound fetches use bounded concurrency (sequential with delay)
- Not production-ready for untrusted multi-tenant abuse without Redis rate limits
