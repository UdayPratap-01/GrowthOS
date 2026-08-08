"""ExecutionEngine — validates then executes structured AI actions honestly."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.automation.action_types import FINANCIAL_ACTIONS, get_action_spec
from app.automation.safety import ActionValidator, PermissionChecker
from app.core.config import get_settings
from app.generation import get_image_provider, get_video_provider
from app.integrations.persistence import get_integration_row
from app.models.ai_ops import Notification
from app.models.automation import ActionExecution, AIAction, AutonomySettings, CreativeAsset, ScheduledPost
from app.models.enums import AIActionStatus, AIActionType, AutonomyMode
from app.models.marketing import Campaign
from app.publishing import get_publisher
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
        tenant = await PermissionChecker().check_tenant(action.organization_id, action.organization_id)
        if not tenant.ok:
            return await self._fail(action, "TENANT_MISMATCH", actor_user_id)

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

        action.status = AIActionStatus.executing
        action.error = None
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
            confirmed = bool(result.get("confirmed"))
            demo = bool(result.get("demo"))
            if not confirmed and not demo:
                raise RuntimeError(result.get("error") or "EXECUTION_NOT_CONFIRMED")

            action.status = AIActionStatus.completed
            action.result = result
            action.executed_at = datetime.now(timezone.utc)
            if demo:
                action.demo_mode = True
                action.result = {**result, "note": "DEMO DATA"}
            execution.status = AIActionStatus.completed
            execution.platform_response = result
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
        if t == AIActionType.generate_recommendation:
            return {"confirmed": True, "demo": True, "note": "DEMO DATA — recommendation recorded as action result"}
        if t == AIActionType.generate_report:
            return {"confirmed": True, "demo": get_settings().demo_mode, "note": "Report generation queued/recorded"}
        if t == AIActionType.send_notification:
            await self._notify(action, title=action.description, body=action.reason)
            return {"confirmed": True, "demo": False}
        if t == AIActionType.create_lead_action:
            return {"confirmed": True, "demo": True, "note": "DEMO DATA — lead follow-up recorded"}
        return {"confirmed": False, "error": f"UNSUPPORTED_ACTION:{t.value}"}

    async def _exec_image(self, action: AIAction) -> dict:
        provider = get_image_provider()
        prompt = (action.payload or {}).get("prompt") or action.description
        result = await provider.generate_image(prompt=prompt, meta=action.payload or {})
        if not result.success and result.error == "IMAGE GENERATION NOT CONFIGURED":
            return {"confirmed": False, "error": "IMAGE GENERATION NOT CONFIGURED"}
        if action.client_id:
            asset = CreativeAsset(
                organization_id=action.organization_id,
                client_id=action.client_id,
                name=f"Image concept — {action.description[:80]}",
                asset_type="image" if not result.demo else "concept",
                platform=action.platform,
                prompt=prompt,
                provider=result.provider,
                status="completed" if result.success else "failed",
                content={"assets": result.assets},
                meta={"message": result.message},
                data_source="demo" if result.demo else "live",
            )
            self.db.add(asset)
            await self.db.flush()
            return {
                "confirmed": result.success,
                "demo": result.demo,
                "creative_asset_id": str(asset.id),
                "message": result.message,
                "error": result.error,
            }
        return {"confirmed": result.success, "demo": result.demo, "message": result.message, "error": result.error}

    async def _exec_video(self, action: AIAction) -> dict:
        provider = get_video_provider()
        prompt = (action.payload or {}).get("prompt") or action.description
        result = await provider.generate_video(prompt=prompt, meta=action.payload or {})
        if not result.success and result.error == "VIDEO GENERATION NOT CONFIGURED":
            return {"confirmed": False, "error": "VIDEO GENERATION NOT CONFIGURED"}
        if action.client_id:
            asset = CreativeAsset(
                organization_id=action.organization_id,
                client_id=action.client_id,
                name=f"Video concept — {action.description[:80]}",
                asset_type="video" if not result.demo else "concept",
                platform=action.platform,
                prompt=prompt,
                provider=result.provider,
                status="completed" if result.success else "failed",
                content={"assets": result.assets},
                meta={"message": result.message},
                data_source="demo" if result.demo else "live",
            )
            self.db.add(asset)
            await self.db.flush()
            return {
                "confirmed": result.success,
                "demo": result.demo,
                "creative_asset_id": str(asset.id),
                "message": result.message,
            }
        return {"confirmed": result.success, "demo": result.demo, "message": result.message, "error": result.error}

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
                # Keep local schedule; do not claim platform schedule.
                row.publish_result = {"error": pub.error, "message": pub.message}
                return {
                    "confirmed": True,
                    "demo": False,
                    "scheduled_post_id": str(row.id),
                    "platform_status": pub.status,
                    "message": "Local schedule stored. Platform schedule: " + (pub.message or ""),
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

    async def _exec_ads_mutation(self, action: AIAction) -> dict:
        """Ads mutations require live connected integration + platform write confirmation."""
        settings = get_settings()
        platform = (action.platform or "").lower()
        provider = "meta" if platform in {"meta", "facebook", "instagram"} else platform
        if provider == "google_ads" or platform == "google":
            provider = "google_ads"

        if settings.demo_mode or action.demo_mode:
            if action.action_type == AIActionType.pause_campaign and action.target_id:
                camp = await self._get_campaign(action)
                if camp:
                    action.previous_state = {"status": camp.status}
                    camp.status = "paused"
            return {
                "confirmed": True,
                "demo": True,
                "message": "DEMO DATA — ads mutation simulated locally; no live platform write.",
                "action_type": action.action_type.value,
                "external_id": None,
            }

        row = await get_integration_row(
            self.db, organization_id=action.organization_id, provider=provider, client_id=action.client_id
        )
        if not row or not row.secret_ref or row.status != "connected":
            row = await get_integration_row(
                self.db, organization_id=action.organization_id, provider=provider, client_id=None
            )
        if not row or not row.secret_ref or row.status != "connected":
            label = provider.upper().replace("_", " ")
            return {"confirmed": False, "error": f"{label} NOT CONNECTED"}

        return {
            "confirmed": False,
            "error": "LIVE ADS WRITE NOT AVAILABLE — connect scopes and enable write adapters.",
        }

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
            if camp:
                camp.status = action.previous_state["status"]
                await self.db.flush()
                return {"success": True, "message": "Local campaign status restored.", "demo": action.demo_mode}
        return {"success": False, "message": "ROLLBACK NOT AVAILABLE"}
