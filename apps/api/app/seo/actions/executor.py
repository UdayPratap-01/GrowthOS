"""SEO action execution — review-only apply to GrowthOS artifacts (M9.13)."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.automation.production_gates import evaluate_production_gates
from app.core.config import get_settings
from app.models.automation import AIAction
from app.models.enums import AIActionType
from app.models.seo_generated_content import SeoGeneratedContent
from app.models.seo_internal_link import SeoInternalLinkOpportunity
from app.models.seo_onpage_optimization import SeoOnPageFinding
from app.models.seo_schema import SeoSchemaArtifact
from app.seo.actions.payload import verify_approved_payload
from app.seo.actions.types import SeoActionSourceType, SeoExecutionCapability


NO_LIVE_PUBLISH = (
    "M9.13 recorded the approved action in GrowthOS only. "
    "No live website, CMS, or external system was modified."
)


class SeoActionExecutor:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def execute(self, action: AIAction) -> dict:
        payload = dict(action.payload or {})
        ok, reason = verify_approved_payload(payload)
        if not ok:
            return {"confirmed": False, "error": reason, "blocked": True}

        capability = payload.get("capability")
        if capability == SeoExecutionCapability.unsupported.value:
            return {"confirmed": False, "error": "UNSUPPORTED_CAPABILITY", "blocked": True}

        from app.services.autonomy_service import AutonomyService

        autonomy = await AutonomyService(self.db).get_effective(
            action.organization_id, action.client_id
        )
        gates = evaluate_production_gates(
            organization_id=action.organization_id,
            client_id=action.client_id,
            platform=action.platform,
            action_type=action.action_type,
            autonomy=autonomy,
            intent="execute",
            app_settings=get_settings(),
        )
        if not gates.allowed:
            return {
                "confirmed": False,
                "error": gates.blocked_code or "GATE_BLOCKED",
                "blocked": True,
                "gate_checks": [c.as_dict() for c in gates.checks],
            }

        stale, stale_reason = await self._artifact_stale(payload)
        if stale:
            return {"confirmed": False, "error": stale_reason or "STALE_ARTIFACT", "blocked": True}

        await self._apply_source_status(payload, action_id=action.id)

        export = {
            "action_type": action.action_type.value,
            "source_type": payload.get("source_type"),
            "source_id": payload.get("source_id"),
            "target": payload.get("target"),
            "changes": payload.get("changes"),
            "exported_at": datetime.now(timezone.utc).isoformat(),
        }
        return {
            "confirmed": True,
            "demo": False,
            "review_only": capability == SeoExecutionCapability.review_only.value,
            "capability": capability,
            "export": export,
            "note": NO_LIVE_PUBLISH,
            "verification": {"verified": True, "method": "growthos_artifact_status"},
        }

    async def _artifact_stale(self, payload: dict) -> tuple[bool, str | None]:
        version = payload.get("artifact_version") or {}
        expected_key = version.get("version_key")
        expected_ts = version.get("updated_at")
        source_type = payload.get("source_type")
        source_id = payload.get("source_id")
        if not source_id:
            return True, "SOURCE_ID_MISSING"

        row = await self._load_source(source_type, UUID(source_id))
        if row is None:
            return True, "SOURCE_NOT_FOUND"

        current_key = getattr(row, "dedupe_key", None) or getattr(row, "generation_key", None) or getattr(row, "analysis_key", None)
        current_ts = row.updated_at.isoformat() if getattr(row, "updated_at", None) else None
        if expected_key and current_key and expected_key != current_key:
            return True, "ARTIFACT_VERSION_KEY_MISMATCH"
        if expected_ts and current_ts and expected_ts != current_ts:
            return True, "ARTIFACT_UPDATED_AFTER_PROPOSAL"
        return False, None

    async def _load_source(self, source_type: str | None, source_id: UUID):
        if source_type == SeoActionSourceType.internal_link_opportunity.value:
            return await self.db.get(SeoInternalLinkOpportunity, source_id)
        if source_type == SeoActionSourceType.onpage_finding.value:
            return await self.db.get(SeoOnPageFinding, source_id)
        if source_type == SeoActionSourceType.schema_artifact.value:
            return await self.db.get(SeoSchemaArtifact, source_id)
        if source_type == SeoActionSourceType.generated_content.value:
            return await self.db.get(SeoGeneratedContent, source_id)
        return None

    async def _apply_source_status(self, payload: dict, *, action_id: UUID) -> None:
        source_type = payload.get("source_type")
        source_id = payload.get("source_id")
        if not source_id:
            return
        row = await self._load_source(source_type, UUID(source_id))
        if row is None:
            return

        if source_type == SeoActionSourceType.internal_link_opportunity.value:
            row.status = "accepted"
        elif source_type == SeoActionSourceType.onpage_finding.value:
            row.status = "accepted"
        elif source_type == SeoActionSourceType.schema_artifact.value:
            row.status = "accepted"
        elif source_type == SeoActionSourceType.generated_content.value:
            # Draft content remains draft — no live publish claim.
            pass

        meta_payload = dict(getattr(row, "__dict__", {}))
        _ = meta_payload  # status update only; action_id tracked on AIAction
