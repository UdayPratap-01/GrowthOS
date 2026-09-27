# M9.11 — SEO Schema Generator / Validator

## Overview

M9.11 generates and validates reviewable JSON-LD schema artifacts for M9.9 generated content and M9.8 content briefs.

**M9.11 does NOT:**

- Publish or deploy schema to live websites
- Inject JSON-LD into CMS or production HTML
- Modify WordPress, Webflow, Shopify, or any external site
- Perform autonomous SEO actions

Pipeline:

```
SeoGeneratedContent + SeoContentBrief + trusted evidence
→ Schema Eligibility Engine
→ Deterministic JSON-LD Generator
→ JSON-LD Validator
→ Content/Evidence Consistency Validator
→ SeoSchemaArtifact + SeoSchemaFinding
```

## Versions

| Constant | Value |
|----------|-------|
| Generation algorithm | `seo_schema_generation_v1` |
| Validation algorithm | `seo_schema_validation_v1` |
| Prompt (reserved) | `seo_schema_prompt_v1` |

## Supported schema types

- Article, BlogPosting, WebPage
- FAQPage (when genuine Q&A exists in content)
- HowTo (when guide has sequential steps)
- Organization (when verified org name + URL exist)
- BreadcrumbList (when verified target URL exists)
- Service, Product, LocalBusiness — eligibility returns `insufficient_data` without verified business/product evidence
- NewsArticle — ineligible unless verified news content exists

## Eligibility

Each type returns `eligible`, `ineligible`, or `insufficient_data`. Types are never generated without passing eligibility.

## No fabrication

The generator omits properties that require verified evidence:

- author, datePublished, dateModified, image, publisher
- price, SKU, availability, ratings, reviews
- address, phone, opening hours, coordinates

Validation rejects fabricated properties if present in submitted JSON-LD.

## Validation

Deterministic checks cover:

- JSON syntax
- `@context` and `@type`
- Required/recommended properties
- URL format
- Content consistency (headline vs title, FAQ pairs, HowTo steps)
- Fabricated property denylist

Finding levels: ERROR, WARNING, INFO.

## Idempotency

`generation_key = sha256(organization | content | content.generation_key | brief.generation_key | schema_type | algorithms)`

Re-running with unchanged inputs replaces prior artifacts for that key.

## API

| Method | Path |
|--------|------|
| POST | `/api/v1/seo/content/{content_id}/schema` |
| GET | `/api/v1/seo/content/{content_id}/schema` |
| POST | `/api/v1/seo/content/{content_id}/schema/validate` |
| GET | `/api/v1/seo/content/{content_id}/schema/{schema_id}` |
| GET | `/api/v1/seo/content/{content_id}/schema/{schema_id}/findings` |

## Security

- Tenant isolation on all queries
- RBAC via existing SEO permissions
- URL validation (syntax only; no schema-time fetching)
- Prompt injection defense: evidence treated as untrusted data
- Audit logging on generate/validate

## M9.12 handoff

M9.12 (Internal-Link Engine) may consume generated content, briefs, M9.10 findings, and M9.11 schema context. M9.11 does not implement internal-link automation.
