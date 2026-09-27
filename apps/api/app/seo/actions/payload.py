"""Immutable SEO action payload helpers (M9.13)."""

from __future__ import annotations

import hashlib
import json
from typing import Any
from uuid import UUID

from app.seo.actions.types import SEO_ACTION_VERSION, SeoActionSourceType, SeoExecutionCapability


def artifact_version_stamp(*, updated_at: Any, version_key: str) -> dict[str, str]:
    ts = updated_at.isoformat() if hasattr(updated_at, "isoformat") else str(updated_at)
    return {"updated_at": ts, "version_key": version_key}


def build_execution_payload(
    *,
    source_type: SeoActionSourceType,
    source_id: UUID,
    capability: SeoExecutionCapability,
    artifact_version: dict[str, str],
    target: dict[str, Any],
    changes: dict[str, Any],
    evidence_refs: list[dict[str, Any]],
    limitations: list[str],
) -> dict[str, Any]:
    return {
        "seo_action_version": SEO_ACTION_VERSION,
        "source_type": source_type.value,
        "source_id": str(source_id),
        "capability": capability.value,
        "artifact_version": artifact_version,
        "target": target,
        "changes": changes,
        "evidence_refs": evidence_refs,
        "limitations": limitations,
    }


def payload_hash(payload: dict[str, Any]) -> str:
    """Hash the execution-relevant subset frozen at approval time."""
    frozen = {
        "seo_action_version": payload.get("seo_action_version"),
        "source_type": payload.get("source_type"),
        "source_id": payload.get("source_id"),
        "capability": payload.get("capability"),
        "artifact_version": payload.get("artifact_version"),
        "target": payload.get("target"),
        "changes": payload.get("changes"),
    }
    raw = json.dumps(frozen, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def stamp_approved_payload(payload: dict[str, Any]) -> dict[str, Any]:
    out = dict(payload)
    out["approved_payload_hash"] = payload_hash(payload)
    return out


def verify_approved_payload(payload: dict[str, Any]) -> tuple[bool, str | None]:
    expected = payload.get("approved_payload_hash")
    if not expected:
        return False, "MISSING_APPROVED_PAYLOAD_HASH"
    actual = payload_hash(payload)
    if actual != expected:
        return False, "PAYLOAD_HASH_MISMATCH"
    return True, None
