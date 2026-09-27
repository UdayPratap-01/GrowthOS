# SEO Approval / Action System (M9.13)

## Overview

M9.13 converts eligible SEO recommendations into controlled, auditable, **user-approved** actions via the existing GrowthOS `ActionService` and `ExecutionEngine`.

**M9.13 requires explicit user approval for executable SEO mutations.**

It does **NOT**:
- auto-approve recommendations
- auto-execute on generation, page load, or automation flags
- publish to CMS or live websites (no writer integration exists)
- implement M9.14 dashboard

## Architecture

```
M9.7–M9.12 artifact (suggested/open/draft)
        ↓
SeoActionService.propose_*()  → eligibility + conflict checks
        ↓
ActionService.create()  → AIAction (PENDING, requires_approval=true)
        ↓
Explicit user approve/reject (RBAC: action_approve)
        ↓
ExecutionEngine → SeoActionExecutor (review_only apply)
        ↓
Artifact status update + export payload (GrowthOS only)
```

## Action types

| AIActionType | Source |
|--------------|--------|
| `SEO_APPLY_INTERNAL_LINK` | `SeoInternalLinkOpportunity` |
| `SEO_APPLY_METADATA` | `SeoOnPageFinding` |
| `SEO_APPLY_SCHEMA` | `SeoSchemaArtifact` |
| `SEO_APPLY_CONTENT` | `SeoGeneratedContent` (future) |

## Execution capability

All SEO actions resolve to **`review_only`** today because no CMS/site writer is configured.

- **REVIEW_ONLY**: approved action records export payload in GrowthOS; artifact marked accepted
- **UNSUPPORTED**: action type not recognized
- **EXECUTABLE**: reserved for future CMS integrations

## Payload immutability

At approval, `approved_payload_hash` is stamped on the action payload. Execution verifies the hash and artifact version before applying.

If the source artifact changes after proposal, execution fails with a stale/conflict reason.

## API

Base: `/api/v1/seo/actions`

| Method | Path | Permission |
|--------|------|------------|
| GET | `/` | auth |
| GET | `/summary` | auth |
| GET | `/{id}` | auth |
| GET | `/{id}/audit` | auth |
| POST | `/{id}/approve` | action_approve |
| POST | `/{id}/reject` | action_approve |
| POST | `/{id}/cancel` | action_approve |
| POST | `/propose/internal-link/{opp_id}` | content_write |
| GET | `/propose/internal-link/{opp_id}/eligibility` | auth |
| POST | `/propose/onpage/{finding_id}` | content_write |
| POST | `/propose/schema/{artifact_id}` | content_write |

## Security

- Tenant-scoped queries on all artifacts and actions
- URL validation reuses M9.1/M9.12 internal URL checks
- SEO actions always require approval regardless of autonomy mode
- Kill switch / production gates evaluated at execution
- Audit events: `seo_action.proposed|approved|rejected|cancelled`

## M9.14 handoff

M9.14 can consume:
- `AIAction` rows for SEO types with status lifecycle
- Action summary counts (pending/completed/review_only)
- Audit trail per action
- Export payloads for dashboard display

M9.13 did not implement M9.14 dashboard or monitoring.

## Version

- Action payload version: `seo_action_v1`
