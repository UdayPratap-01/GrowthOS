"""SEO schema generator/validator service (M9.11)."""

from __future__ import annotations

import hashlib
import json
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.seo_content_brief import SeoContentBrief
from app.models.seo_generated_content import SeoGeneratedContent
from app.models.seo_schema import SeoSchemaArtifact, SeoSchemaFinding
from app.schemas.seo_schema import SeoSchemaArtifactOut, SeoSchemaFindingOut, SeoSchemaListOut
from app.security.audit import write_audit
from app.seo.schema.eligibility import evaluate_eligibility
from app.seo.schema.generator import generate_schemas
from app.seo.schema.thresholds import (
    DEFAULT_SCHEMA_LIMITS,
    GENERATION_ALGORITHM,
    PROMPT_VERSION,
    SUPPORTED_SCHEMA_TYPES,
    VALIDATION_ALGORITHM,
)
from app.seo.schema.validator import validate_schema
from app.services.seo_content_brief_service import SeoContentBriefService
from app.services.seo_generated_content_service import SeoGeneratedContentService

DISCLAIMER = (
    "JSON-LD schema artifacts are review/export drafts within GrowthOS. "
    "They are not deployed to live websites automatically."
)

NO_PUBLISHING_NOTE = "M9.11 generates and validates JSON-LD only. It does not publish, inject, or modify live websites."


class SeoSchemaService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def generate(
        self,
        *,
        organization_id: UUID,
        content_id: UUID,
        user_id: UUID | None = None,
        schema_types: list[str] | None = None,
    ) -> dict:
        content, brief = await self._load_content_and_brief(organization_id=organization_id, content_id=content_id)
        if len(content.content or "") > 100_000:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="content_too_large")

        eligibility = evaluate_eligibility(content, brief)
        if schema_types:
            allowed = {t for t in schema_types if t in SUPPORTED_SCHEMA_TYPES}
            eligibility = [e for e in eligibility if e.schema_type in allowed]

        generated = generate_schemas(content, brief, eligibility)
        generated = generated[: DEFAULT_SCHEMA_LIMITS.max_schema_types_per_run]

        artifact_ids: list[UUID] = []
        for item in generated:
            gen_key = _generation_key(
                organization_id=organization_id,
                content=content,
                brief=brief,
                schema_type=item.schema_type,
            )
            await self.db.execute(
                delete(SeoSchemaArtifact).where(
                    SeoSchemaArtifact.organization_id == organization_id,
                    SeoSchemaArtifact.generation_key == gen_key,
                )
            )

            validation_status = "not_generated"
            errors: list = []
            warnings: list = []
            findings: list = []
            if item.json_ld is not None:
                raw = json.dumps(item.json_ld)
                if len(raw) > DEFAULT_SCHEMA_LIMITS.max_json_ld_bytes:
                    raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="schema_too_large")
                validation_status, errors, warnings, findings = validate_schema(item, content, brief)

            limitations = [NO_PUBLISHING_NOTE, "Omitted properties require verified evidence; never fabricated."]
            artifact = SeoSchemaArtifact(
                organization_id=organization_id,
                generated_content_id=content.id,
                content_brief_id=brief.id,
                schema_type=item.schema_type,
                status="validated" if validation_status in {"valid", "warnings"} else "draft",
                json_ld=item.json_ld,
                validation_status=validation_status,
                validation_errors=errors,
                validation_warnings=warnings,
                eligibility_status=item.eligibility.status,
                eligibility_reasons=item.eligibility.reasons,
                evidence_refs=item.evidence_refs,
                source_fields=item.source_fields,
                limitations=limitations,
                generation_key=gen_key,
                generation_algorithm_version=GENERATION_ALGORITHM,
                validation_algorithm_version=VALIDATION_ALGORITHM,
                prompt_version=PROMPT_VERSION,
            )
            self.db.add(artifact)
            await self.db.flush()

            for draft in findings[: DEFAULT_SCHEMA_LIMITS.max_findings]:
                self.db.add(
                    SeoSchemaFinding(
                        organization_id=organization_id,
                        artifact_id=artifact.id,
                        generated_content_id=content.id,
                        finding_type=draft.finding_type,
                        category=draft.category,
                        severity=draft.severity,
                        level=draft.level,
                        title=draft.title[:255],
                        summary=draft.summary,
                        rationale=draft.rationale,
                        property_path=draft.property_path,
                        evidence_refs=draft.evidence_refs,
                        dedupe_key=draft.dedupe_key(),
                    )
                )
            artifact_ids.append(artifact.id)

        await write_audit(
            self.db,
            action="seo_schema.generate",
            organization_id=organization_id,
            user_id=user_id,
            resource_type="seo_schema_artifact",
            resource_id=str(content_id),
            details={"content_id": str(content_id), "artifact_count": len(artifact_ids)},
        )
        await self.db.flush()

        return {
            "content_id": content_id,
            "artifact_ids": artifact_ids,
            "generation_algorithm": GENERATION_ALGORITHM,
            "validation_algorithm": VALIDATION_ALGORITHM,
            "count": len(artifact_ids),
        }

    async def list_schemas(self, *, organization_id: UUID, content_id: UUID) -> SeoSchemaListOut:
        content, brief = await self._load_content_and_brief(organization_id=organization_id, content_id=content_id)
        rows = list(
            (
                await self.db.execute(
                    select(SeoSchemaArtifact)
                    .where(
                        SeoSchemaArtifact.organization_id == organization_id,
                        SeoSchemaArtifact.generated_content_id == content_id,
                    )
                    .order_by(SeoSchemaArtifact.created_at.desc())
                )
            )
            .scalars()
            .all()
        )
        if not rows:
            eligibility = evaluate_eligibility(content, brief)
            return SeoSchemaListOut(
                artifacts=[],
                eligibility_summary=[{"schema_type": e.schema_type, "status": e.status, "reasons": e.reasons} for e in eligibility],
                disclaimer=DISCLAIMER,
            )
        latest_by_type: dict[str, SeoSchemaArtifact] = {}
        for row in rows:
            if row.schema_type not in latest_by_type:
                latest_by_type[row.schema_type] = row
        eligibility = evaluate_eligibility(content, brief)
        return SeoSchemaListOut(
            artifacts=[SeoSchemaArtifactOut.model_validate(r) for r in latest_by_type.values()],
            eligibility_summary=[{"schema_type": e.schema_type, "status": e.status, "reasons": e.reasons} for e in eligibility],
            disclaimer=DISCLAIMER,
        )

    async def get_schema(self, *, organization_id: UUID, content_id: UUID, schema_id: UUID) -> SeoSchemaArtifactOut:
        await SeoGeneratedContentService(self.db).get_content(organization_id=organization_id, content_id=content_id)
        row = await self.db.scalar(
            select(SeoSchemaArtifact).where(
                SeoSchemaArtifact.id == schema_id,
                SeoSchemaArtifact.organization_id == organization_id,
                SeoSchemaArtifact.generated_content_id == content_id,
            ).limit(1)
        )
        if not row:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="schema_not_found")
        return SeoSchemaArtifactOut.model_validate(row)

    async def validate(
        self,
        *,
        organization_id: UUID,
        content_id: UUID,
        user_id: UUID | None = None,
        artifact_id: UUID | None = None,
    ) -> dict:
        content, brief = await self._load_content_and_brief(organization_id=organization_id, content_id=content_id)
        q = select(SeoSchemaArtifact).where(
            SeoSchemaArtifact.organization_id == organization_id,
            SeoSchemaArtifact.generated_content_id == content_id,
        )
        if artifact_id:
            q = q.where(SeoSchemaArtifact.id == artifact_id)
        artifacts = list((await self.db.execute(q)).scalars().all())
        if not artifacts:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="schema_not_found")

        validated = 0
        for artifact in artifacts:
            if artifact.json_ld is None:
                continue
            from app.seo.schema.types import EligibilityResult, GeneratedSchema

            generated = GeneratedSchema(
                schema_type=artifact.schema_type,
                eligibility=EligibilityResult(artifact.schema_type, artifact.eligibility_status, artifact.eligibility_reasons),
                json_ld=artifact.json_ld,
            )
            validation_status, errors, warnings, findings = validate_schema(generated, content, brief)
            artifact.validation_status = validation_status
            artifact.validation_errors = errors
            artifact.validation_warnings = warnings
            artifact.validation_algorithm_version = VALIDATION_ALGORITHM
            artifact.status = "validated" if validation_status in {"valid", "warnings"} else "draft"

            await self.db.execute(
                delete(SeoSchemaFinding).where(
                    SeoSchemaFinding.organization_id == organization_id,
                    SeoSchemaFinding.artifact_id == artifact.id,
                )
            )
            for draft in findings[: DEFAULT_SCHEMA_LIMITS.max_findings]:
                self.db.add(
                    SeoSchemaFinding(
                        organization_id=organization_id,
                        artifact_id=artifact.id,
                        generated_content_id=content.id,
                        finding_type=draft.finding_type,
                        category=draft.category,
                        severity=draft.severity,
                        level=draft.level,
                        title=draft.title[:255],
                        summary=draft.summary,
                        rationale=draft.rationale,
                        property_path=draft.property_path,
                        evidence_refs=draft.evidence_refs,
                        dedupe_key=draft.dedupe_key(),
                    )
                )
            validated += 1

        await write_audit(
            self.db,
            action="seo_schema.validate",
            organization_id=organization_id,
            user_id=user_id,
            resource_type="seo_schema_artifact",
            resource_id=str(content_id),
            details={"validated_count": validated},
        )
        await self.db.flush()
        return {"content_id": content_id, "validated_count": validated, "validation_algorithm": VALIDATION_ALGORITHM}

    async def list_findings(
        self, *, organization_id: UUID, content_id: UUID, schema_id: UUID
    ) -> list[SeoSchemaFindingOut]:
        await self.get_schema(organization_id=organization_id, content_id=content_id, schema_id=schema_id)
        rows = list(
            (
                await self.db.execute(
                    select(SeoSchemaFinding)
                    .where(
                        SeoSchemaFinding.organization_id == organization_id,
                        SeoSchemaFinding.artifact_id == schema_id,
                        SeoSchemaFinding.generated_content_id == content_id,
                    )
                    .order_by(SeoSchemaFinding.created_at.desc())
                )
            )
            .scalars()
            .all()
        )
        return [SeoSchemaFindingOut.model_validate(r) for r in rows]

    async def _load_content_and_brief(
        self, *, organization_id: UUID, content_id: UUID
    ) -> tuple[SeoGeneratedContent, SeoContentBrief]:
        content = await SeoGeneratedContentService(self.db).get_content(
            organization_id=organization_id, content_id=content_id
        )
        if content.status == "archived":
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="content_archived")
        brief = await SeoContentBriefService(self.db).get_brief(
            organization_id=organization_id, brief_id=content.content_brief_id
        )
        return content, brief


def _generation_key(
    *,
    organization_id: UUID,
    content: SeoGeneratedContent,
    brief: SeoContentBrief,
    schema_type: str,
) -> str:
    raw = f"{organization_id}|{content.id}|{content.generation_key}|{brief.generation_key}|{schema_type}|{GENERATION_ALGORITHM}|{VALIDATION_ALGORITHM}"
    return hashlib.sha256(raw.encode()).hexdigest()[:64]
