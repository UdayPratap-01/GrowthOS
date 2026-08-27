"""ExecutionEngine — validates then executes structured AI actions honestly."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.automation.action_types import FINANCIAL_ACTIONS, get_action_spec
from app.automation.idempotency import (
    execution_idempotency_key,
    sanitize_platform_response,
    try_claim_action_for_execution,
)
from app.automation.safety import ActionValidator, PermissionChecker
from app.automation.tenant import TargetValidator
from app.automation.provider_reconciliation import (
    build_reconciliation_metadata,
    enqueue_provider_reconciliation,
)
from app.publishing.provider_errors import is_ambiguous_error_code
from app.core.config import get_settings
from app.jobs.queue import JobQueue
from app.jobs.registry import REPORT_GENERATE
from app.models.ai_ops import Notification
from app.models.automation import ActionExecution, AIAction, AutonomySettings, CreativeAsset, ScheduledPost
from app.models.enums import AIActionStatus, AIActionType, AutonomyMode
from app.models.marketing import Campaign
from app.models.organization import Organization
from app.publishing import get_publisher
from app.publishing.ads_executor import AdsExecutor
from app.security.audit import write_audit


class ExecutionEngine:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def execute(
        self,
        action: AIAction,
        *,
        actor_user_id: UUID | None,
        force: bool = False,
    ) -> AIAction:
        if action.status == AIActionStatus.completed and not force:
            return action

        tenant = await PermissionChecker().check_tenant(action.organization_id, action.organization_id)
        if not tenant.ok:
            return await self._fail(action, "TENANT_MISMATCH", actor_user_id)

        target_check = await TargetValidator(self.db).validate_action_targets(action)
        if not target_check.ok:
            return await self._fail(action, ";".join(target_check.errors), actor_user_id)

        settings = await self._load_settings(action.organization_id, action.client_id)
        if action.status not in {AIActionStatus.approved, AIActionStatus.pending, AIActionStatus.failed}:
            return await self._fail(action, f"INVALID_STATUS:{action.status.value}", actor_user_id)

        if action.requires_approval and action.status != AIActionStatus.approved and not force:
            action.status = AIActionStatus.pending
            await self.db.flush()
            return action

        if settings.autonomy_mode == AutonomyMode.copilot and action.status != AIActionStatus.approved:
            return await self._fail(action, "COPILOT_REQUIRES_APPROVAL", actor_user_id)

        if not settings.automation_enabled and action.action_type in FINANCIAL_ACTIONS:
            if not force and action.status != AIActionStatus.approved:
                return await self._fail(action, "AUTOMATION_DISABLED", actor_user_id)

        validation = await ActionValidator(self.db).validate(
            organization_id=action.organization_id,
            settings=settings,
            action_type=action.action_type,
            platform=action.platform,
            estimated_cost=action.estimated_cost,
            client_id=action.client_id,
            payload=action.payload or {},
        )
        # Re-validate all safety rules on execute (never trust a stale approval alone)
        if not validation.ok:
            return await self._fail(action, ";".join(validation.errors), actor_user_id)

        claim = await try_claim_action_for_execution(self.db, action, force=force)
        if claim == "completed":
            return action
        if claim == "executing":
            return action
        if claim == "blocked":
            return await self._fail(action, f"INVALID_STATUS:{action.status.value}", actor_user_id)

        execution = ActionExecution(
            organization_id=action.organization_id,
            action_id=action.id,
            status=AIActionStatus.executing,
            started_at=datetime.now(timezone.utc),
            is_demo=bool(action.demo_mode or get_settings().demo_mode),
        )
        self.db.add(execution)
        await self.db.flush()

        try:
            result = await self._dispatch(action, settings)
            if result.get("ambiguous") or is_ambiguous_error_code(result.get("error_code")):
                return await self._fail_ambiguous(
                    action,
                    result,
                    execution,
                    actor_user_id=actor_user_id,
                )
            confirmed = bool(result.get("confirmed"))
            demo = bool(result.get("demo"))
            if not confirmed and not demo:
                raise RuntimeError(result.get("error") or "EXECUTION_NOT_CONFIRMED")

            action.status = AIActionStatus.completed
            safe_result = dict(result)
            if "platform_response" in safe_result:
                safe_result["platform_response"] = sanitize_platform_response(safe_result["platform_response"])
            action.result = safe_result
            action.executed_at = datetime.now(timezone.utc)
            action.executing_at = None
            if result.get("external_id"):
                action.external_id = str(result["external_id"])
            if demo:
                action.demo_mode = True
                action.result = {**safe_result, "note": "DEMO DATA"}
            execution.status = AIActionStatus.completed
            execution.platform_response = safe_result
            execution.finished_at = datetime.now(timezone.utc)
            execution.is_demo = demo
            await write_audit(
                self.db,
                organization_id=action.organization_id,
                user_id=actor_user_id,
                action="ai_action.executed",
                resource_type="ai_action",
                resource_id=str(action.id),
                details={"action_type": action.action_type.value, "demo": demo, "result_keys": list(result.keys())},
            )
            await self._notify(
                action,
                title=f"Action completed: {action.action_type.value}",
                body=action.description + (" (DEMO DATA)" if demo else ""),
            )
        except Exception as exc:
            action.status = AIActionStatus.failed
            action.error = str(exc)
            action.executing_at = None
            action.retry_count = int(action.retry_count or 0) + 1
            execution.status = AIActionStatus.failed
            execution.error_message = str(exc)
            execution.error_code = "EXECUTION_FAILED"
            execution.finished_at = datetime.now(timezone.utc)
            await write_audit(
                self.db,
                organization_id=action.organization_id,
                user_id=actor_user_id,
                action="ai_action.failed",
                resource_type="ai_action",
                resource_id=str(action.id),
                details={"error": str(exc)},
            )
            await self._notify(action, title=f"Action failed: {action.action_type.value}", body=str(exc))

        await self.db.flush()
        await self.db.refresh(action)
        return action

    async def _dispatch(self, action: AIAction, settings: AutonomySettings) -> dict:
        t = action.action_type
        if t == AIActionType.generate_image:
            return await self._exec_image(action)
        if t == AIActionType.generate_video:
            return await self._exec_video(action)
        if t == AIActionType.create_creative:
            return await self._exec_creative(action)
        if t == AIActionType.generate_creative_variations:
            return await self._exec_creative_variations(action)
        if t == AIActionType.create_content:
            return await self._exec_create_content(action)
        if t == AIActionType.schedule_content:
            return await self._exec_schedule(action)
        if t == AIActionType.publish_content:
            return await self._exec_publish(action)
        if t in {
            AIActionType.create_campaign,
            AIActionType.create_ad,
            AIActionType.create_ad_set,
            AIActionType.update_budget,
            AIActionType.pause_campaign,
            AIActionType.resume_campaign,
            AIActionType.update_campaign,
            AIActionType.optimize_campaign,
        }:
            return await self._exec_ads_mutation(action)
        if t == AIActionType.update_content:
            return await self._exec_update_content(action)
        if t == AIActionType.generate_recommendation:
            return {"confirmed": True, "demo": True, "note": "DEMO DATA — recommendation recorded as action result"}
        if t == AIActionType.generate_report:
            return await self._exec_generate_report(action)
        if t == AIActionType.send_notification:
            await self._notify(action, title=action.description, body=action.reason)
            return {"confirmed": True, "demo": False}
        if t == AIActionType.create_lead_action:
            return {"confirmed": True, "demo": True, "note": "DEMO DATA — lead follow-up recorded"}
        return {"confirmed": False, "error": f"UNSUPPORTED_ACTION:{t.value}"}

    async def _exec_image(self, action: AIAction) -> dict:
        if not action.client_id:
            return {"confirmed": False, "error": "CLIENT_REQUIRED"}
        from app.services.media_generation_service import MediaGenerationService

        org = await self.db.get(Organization, action.organization_id)
        if not org:
            return {"confirmed": False, "error": "ORG_NOT_FOUND"}
        prompt = (action.payload or {}).get("prompt") or action.description
        result = await MediaGenerationService(self.db).enqueue_images(
            org,
            client_id=action.client_id,
            prompt=prompt,
            aspect_ratio=(action.payload or {}).get("aspect_ratio") or "1:1",
            quantity=1,
            platform=action.platform,
            idempotency_key=(action.payload or {}).get("idempotency_key") or f"action:{action.id}",
        )
        ok = result.get("status") == "COMPLETED" and bool(result.get("assets"))
        return {
            "confirmed": ok,
            "demo": bool(result.get("demo")),
            "job_id": result.get("job_id"),
            "assets": result.get("assets") or [],
            "message": result.get("message"),
            "error": result.get("error") if not ok else None,
        }

    async def _exec_video(self, action: AIAction) -> dict:
        if not action.client_id:
            return {"confirmed": False, "error": "CLIENT_REQUIRED"}
        from app.services.media_generation_service import MediaGenerationService

        org = await self.db.get(Organization, action.organization_id)
        if not org:
            return {"confirmed": False, "error": "ORG_NOT_FOUND"}
        prompt = (action.payload or {}).get("prompt") or action.description
        result = await MediaGenerationService(self.db).enqueue_video(
            org,
            client_id=action.client_id,
            prompt=prompt,
            aspect_ratio=(action.payload or {}).get("aspect_ratio") or "9:16",
            duration_seconds=int((action.payload or {}).get("duration_seconds") or 10),
            platform=action.platform,
            idempotency_key=(action.payload or {}).get("idempotency_key") or f"action:{action.id}",
        )
        ok = result.get("status") == "COMPLETED" and bool(result.get("assets"))
        return {
            "confirmed": ok,
            "demo": bool(result.get("demo")),
            "job_id": result.get("job_id"),
            "provider_job_id": result.get("provider_job_id"),
            "assets": result.get("assets") or [],
            "message": result.get("message"),
            "error": result.get("error") if not ok else None,
        }

    async def _exec_creative(self, action: AIAction) -> dict:
        if not action.client_id:
            return {"confirmed": False, "error": "CLIENT_REQUIRED"}
        asset = CreativeAsset(
            organization_id=action.organization_id,
            client_id=action.client_id,
            name=(action.payload or {}).get("name") or action.description[:120],
            asset_type="concept",
            platform=action.platform,
            prompt=(action.payload or {}).get("prompt"),
            provider="creative_agent",
            status="draft",
            content=action.payload or {},
            meta={"reason": action.reason, "evidence": action.evidence},
            data_source="demo" if (action.demo_mode or get_settings().demo_mode) else "live",
        )
        self.db.add(asset)
        await self.db.flush()
        return {
            "confirmed": True,
            "demo": asset.data_source == "demo",
            "creative_asset_id": str(asset.id),
            "note": "Creative concept stored. Image/video bytes require a configured provider.",
        }

    async def _exec_creative_variations(self, action: AIAction) -> dict:
        if not action.client_id:
            return {"confirmed": False, "error": "CLIENT_REQUIRED"}
        variations = (action.payload or {}).get("variations") or []
        created_ids: list[str] = []
        demo = bool(action.demo_mode or get_settings().demo_mode)
        for idx, item in enumerate(variations[:20]):
            if isinstance(item, str):
                content = {"text": item, "index": idx}
                name = item[:80]
            else:
                content = dict(item)
                name = str(content.get("headline") or content.get("hook") or f"Variation {idx + 1}")[:255]
            asset = CreativeAsset(
                organization_id=action.organization_id,
                client_id=action.client_id,
                name=name,
                asset_type="variation",
                platform=action.platform,
                prompt=(action.payload or {}).get("prompt"),
                provider="creative_variation_engine",
                status="draft",
                content=content,
                meta={"parent_action": str(action.id), "index": idx},
                data_source="demo" if demo else "live",
            )
            self.db.add(asset)
            await self.db.flush()
            created_ids.append(str(asset.id))
        return {
            "confirmed": True,
            "demo": demo,
            "creative_asset_ids": created_ids,
            "note": "Creative variations stored as draft assets. Not published.",
        }

    async def _exec_create_content(self, action: AIAction) -> dict:
        # Content body stored on action result; Content Studio remains source of SocialPost records.
        return {
            "confirmed": True,
            "demo": bool(action.demo_mode or get_settings().demo_mode),
            "content": (action.payload or {}).get("content") or {"caption": action.description},
            "note": "Content draft prepared as structured action payload.",
        }

    async def _exec_schedule(self, action: AIAction) -> dict:
        if not action.client_id or not action.platform:
            return {"confirmed": False, "error": "CLIENT_AND_PLATFORM_REQUIRED"}
        scheduled_for = (action.payload or {}).get("scheduled_for")
        if not scheduled_for:
            return {"confirmed": False, "error": "SCHEDULED_FOR_REQUIRED"}
        when = datetime.fromisoformat(scheduled_for.replace("Z", "+00:00"))
        row = ScheduledPost(
            organization_id=action.organization_id,
            client_id=action.client_id,
            platform=action.platform,
            scheduled_for=when,
            status="scheduled",
            content=(action.payload or {}).get("content") or {"caption": action.description},
            action_id=action.id,
        )
        self.db.add(row)
        await self.db.flush()
        publisher = get_publisher(self.db, action.platform)
        if publisher:
            pub = await publisher.schedule(
                content=row.content,
                scheduled_for=scheduled_for,
                organization_id=action.organization_id,
                client_id=action.client_id,
            )
            if pub.demo:
                row.status = "demo_scheduled"
                row.publish_result = {"note": "DEMO DATA", **pub.platform_response}
                return {
                    "confirmed": True,
                    "demo": True,
                    "scheduled_post_id": str(row.id),
                    "message": pub.message,
                }
            if not pub.success:
                # Keep local schedule row but do not mark the action as platform-confirmed.
                row.publish_result = sanitize_platform_response(
                    {"error": pub.error, "message": pub.message, "status": pub.status}
                )
                return {
                    "confirmed": False,
                    "demo": False,
                    "scheduled_post_id": str(row.id),
                    "platform_status": pub.status,
                    "error": pub.error or pub.message,
                    "message": "Local schedule stored. Platform schedule was not confirmed.",
                }
        return {"confirmed": True, "demo": False, "scheduled_post_id": str(row.id)}

    async def _exec_publish(self, action: AIAction) -> dict:
        if not action.platform:
            return {"confirmed": False, "error": "PLATFORM_REQUIRED"}
        publisher = get_publisher(self.db, action.platform)
        if not publisher:
            return {"confirmed": False, "error": "INTEGRATION NOT CONNECTED"}
        pub = await publisher.publish(
            content=(action.payload or {}).get("content") or {"caption": action.description},
            organization_id=action.organization_id,
            client_id=action.client_id,
        )
        if pub.demo:
            return {
                "confirmed": True,
                "demo": True,
                "message": pub.message,
                "platform_response": pub.platform_response,
            }
        if not pub.success or not pub.external_id:
            return {"confirmed": False, "error": pub.error or pub.message, "platform_response": pub.platform_response}
        return {
            "confirmed": True,
            "demo": False,
            "external_id": pub.external_id,
            "platform_response": pub.platform_response,
        }

    async def _exec_update_content(self, action: AIAction) -> dict:
        content = (action.payload or {}).get("content")
        if not content:
            return {"confirmed": False, "error": "CONTENT_REQUIRED"}
        return {
            "confirmed": True,
            "demo": bool(action.demo_mode or get_settings().demo_mode),
            "content": content,
            "note": "Content update stored on action result; not published until a publish action runs.",
        }

    async def _exec_generate_report(self, action: AIAction) -> dict:
        if not action.client_id:
            return {"confirmed": False, "error": "CLIENT_REQUIRED"}
        period_days = int((action.payload or {}).get("period_days") or 7)
        job = await JobQueue(self.db).enqueue(
            job_type=REPORT_GENERATE,
            payload={
                "client_id": str(action.client_id),
                "user_id": str(action.approved_by) if action.approved_by else None,
                "period_days": period_days,
                "action_id": str(action.id),
            },
            organization_id=action.organization_id,
            dedupe_key=execution_idempotency_key(action.id),
        )
        demo = bool(action.demo_mode or get_settings().demo_mode)
        return {
            "confirmed": True,
            "demo": demo,
            "job_id": str(job.id),
            "status": job.status.value if hasattr(job.status, "value") else str(job.status),
            "note": "Report generation queued for background worker.",
        }

    async def _exec_ads_mutation(self, action: AIAction) -> dict:
        """Ads mutations delegate to AdsExecutor — real platform confirmation required."""
        campaign = await self._get_campaign(action)
        if action.target_id and campaign is None:
            # target may be opaque platform id; still attempt executor path
            pass
        elif action.target_id and campaign and action.client_id and campaign.client_id != action.client_id:
            return {"confirmed": False, "error": "TENANT_MISMATCH"}

        if action.action_type == AIActionType.pause_campaign and campaign:
            action.previous_state = {"status": campaign.status}

        result = await AdsExecutor(self.db).execute(action, campaign=campaign)
        payload = result.to_dict()
        payload["action_type"] = action.action_type.value
        return payload

    async def _get_campaign(self, action: AIAction) -> Campaign | None:
        try:
            cid = UUID(str(action.target_id))
        except Exception:
            return None
        return await self.db.scalar(
            select(Campaign).where(
                Campaign.id == cid,
                Campaign.organization_id == action.organization_id,
            )
        )

    async def _load_settings(self, organization_id: UUID, client_id: UUID | None) -> AutonomySettings:
        from app.services.autonomy_service import AutonomyService

        return await AutonomyService(self.db).get_effective(organization_id, client_id)

    async def _fail(self, action: AIAction, error: str, actor_user_id: UUID | None) -> AIAction:
        action.status = AIActionStatus.failed
        action.error = error
        action.executing_at = None
        await write_audit(
            self.db,
            organization_id=action.organization_id,
            user_id=actor_user_id,
            action="ai_action.failed",
            resource_type="ai_action",
            resource_id=str(action.id),
            details={"error": error},
        )
        await self.db.flush()
        await self.db.refresh(action)
        return action

    async def _fail_ambiguous(
        self,
        action: AIAction,
        result: dict,
        execution: ActionExecution,
        *,
        actor_user_id: UUID | None,
    ) -> AIAction:
        """Provider mutation outcome unknown — do not treat as confirmed failure."""
        error_code = result.get("error_code") or "PROVIDER_AMBIGUOUS"
        message = result.get("message") or result.get("error") or "Provider state unknown"
        external_id = result.get("external_id") or action.external_id
        provider = (action.platform or "unknown").lower()
        if provider in {"google", "google_ads"}:
            provider_key = "google_ads"
        elif provider in {"meta", "facebook", "instagram"}:
            provider_key = "meta"
        else:
            provider_key = provider

        recon = build_reconciliation_metadata(
            provider=provider_key,
            operation=action.action_type.value,
            external_id=external_id,
            error_code=error_code,
            platform=action.platform,
        )
        safe_result = {
            **result,
            "confirmed": False,
            "ambiguous": True,
            "reconciliation": recon,
        }
        if "platform_response" in safe_result:
            safe_result["platform_response"] = sanitize_platform_response(safe_result["platform_response"])

        action.status = AIActionStatus.failed
        action.error = f"PROVIDER_STATE_UNKNOWN: {message}"
        action.executing_at = None
        action.result = safe_result
        if external_id:
            action.external_id = str(external_id)

        execution.status = AIActionStatus.failed
        execution.error_code = error_code
        execution.error_message = action.error
        execution.finished_at = datetime.now(timezone.utc)
        execution.platform_response = safe_result.get("platform_response") or {}

        await write_audit(
            self.db,
            organization_id=action.organization_id,
            user_id=actor_user_id,
            action="ai_action.ambiguous",
            resource_type="ai_action",
            resource_id=str(action.id),
            details={
                "trigger": "execution",
                "provider": provider_key,
                "operation": action.action_type.value,
                "error_code": error_code,
                "external_id": external_id,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            },
        )
        await enqueue_provider_reconciliation(
            self.db,
            action_id=action.id,
            organization_id=action.organization_id,
        )
        await self._notify(
            action,
            title=f"Action needs reconciliation: {action.action_type.value}",
            body=message,
        )
        await self.db.flush()
        await self.db.refresh(action)
        return action

    async def _notify(self, action: AIAction, *, title: str, body: str) -> None:
        self.db.add(
            Notification(
                organization_id=action.organization_id,
                title=title,
                body=body,
                meta={"action_id": str(action.id), "action_type": action.action_type.value, "demo": action.demo_mode},
            )
        )


class RollbackHandler:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def rollback(self, action: AIAction) -> dict:
        spec = get_action_spec(action.action_type)
        if not spec.reversible or not action.previous_state:
            return {"success": False, "message": "ROLLBACK NOT AVAILABLE"}
        if action.action_type == AIActionType.pause_campaign and action.previous_state.get("status") and action.target_id:
            camp = await ExecutionEngine(self.db)._get_campaign(action)
            if not camp:
                return {"success": False, "message": "TARGET_NOT_FOUND"}
            if action.client_id and camp.client_id != action.client_id:
                return {"success": False, "message": "TENANT_MISMATCH"}
            camp.status = action.previous_state["status"]
            await self.db.flush()
            return {"success": True, "message": "Local campaign status restored.", "demo": action.demo_mode}
        return {"success": False, "message": "ROLLBACK NOT AVAILABLE"}
