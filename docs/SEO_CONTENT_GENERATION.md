# M9.9 — AI SEO Content Generation

## Overview

M9.9 converts an M9.8 **SeoContentBrief** into draft SEO content. Output is a **GrowthOS draft artifact** — not published content.

Pipeline:

```
M9.8 SeoContentBrief + provenance
→ bounded generation context
→ AI content generation
→ structured schema validation
→ brief fidelity validation
→ SeoGeneratedContent (draft)
```

## Versions

| Constant | Value |
|----------|-------|
| Algorithm | `seo_content_generation_v1` |
| Prompt | `seo_content_generation_prompt_v1` |

## M9.8 dependency

Generation requires `content_brief_id`. The server loads the brief, validates status (`ready` or `draft`), and builds context from brief fields only. Clients cannot supply alternate evidence.

## Content types

- `informational_article`
- `blog_article`
- `guide`
- `comparison`
- `service_content`
- `landing_page_draft`
- `content_refresh`

## Brief fidelity

Post-generation checks:

- Primary keyword appears when not `unavailable`
- Outline headings substantially reflected in sections
- Required questions addressed when present
- Required entities represented when specified
- Internal links only from brief `internal_link_targets`
- No fabricated metric patterns (rankings, search volume, etc.)
- Meta title ≤ 70 chars, meta description ≤ 160 chars

## Factuality rules

The AI must not invent business facts, statistics, rankings, traffic, backlinks, testimonials, or competitor metrics.

## No publishing

M9.9 explicitly does **NOT**:

- Publish to CMS or website
- Modify live metadata
- Deploy pages
- Execute autonomous SEO actions

Every generated record includes a no-publishing limitation note.

## API

Base path: `/api/v1/seo/content`

| Method | Path | Description |
|--------|------|-------------|
| POST | `/generate` | Generate from `content_brief_id` |
| GET | `/` | List drafts |
| GET | `/{id}` | Content detail |
| GET | `/{id}/source` | Brief/evidence provenance |
| POST | `/{id}/archive` | Archive draft |

## Frontend

Route: `/seo/content`

Generate from a brief, review draft body, metadata, source evidence, and generation metadata.

## M9.10 handoff

`SeoGeneratedContent` exposes content ID, brief ID, recommendation ID, title, structured sections, headings, keywords, topic, target URL, metadata, internal links, evidence refs, limitations, and generation metadata for on-page optimization.

## Known limitations

- Draft quality depends on brief completeness and AI provider
- Not externally fact-checked
- Does not auto-publish
