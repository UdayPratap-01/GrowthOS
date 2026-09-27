"""JSON-LD schema validator (M9.11)."""

from __future__ import annotations

import json
from typing import Any
from urllib.parse import urlparse

from app.models.seo_content_brief import SeoContentBrief
from app.models.seo_generated_content import SeoGeneratedContent
from app.seo.schema.thresholds import FABRICATED_PROPERTY_DENYLIST, SCHEMA_CONTEXT, SUPPORTED_SCHEMA_TYPES
from app.seo.schema.types import GeneratedSchema, SchemaFindingDraft


def validate_schema(
    generated: GeneratedSchema,
    content: SeoGeneratedContent,
    brief: SeoContentBrief,
) -> tuple[str, list[dict], list[dict], list[SchemaFindingDraft]]:
    findings: list[SchemaFindingDraft] = []
    errors: list[dict] = []
    warnings: list[dict] = []

    if generated.eligibility.status != "eligible" or generated.json_ld is None:
        return "not_generated", errors, warnings, findings

    doc = generated.json_ld
    findings.extend(_validate_syntax(doc))
    findings.extend(_validate_structure(doc))
    findings.extend(_validate_no_fabricated_properties(doc))
    findings.extend(_validate_content_consistency(doc, generated.schema_type, content, brief))

    for f in findings:
        entry = {"code": f.finding_type, "message": f.summary, "property": f.property_path}
        if f.level == "ERROR":
            errors.append(entry)
        elif f.level == "WARNING":
            warnings.append(entry)

    if any(f.level == "ERROR" for f in findings):
        status = "invalid"
    elif warnings:
        status = "warnings"
    else:
        status = "valid"
    return status, errors, warnings, findings


def validate_raw_json_ld(
    json_ld: dict[str, Any],
    *,
    schema_type: str,
    content: SeoGeneratedContent,
    brief: SeoContentBrief,
) -> tuple[str, list[SchemaFindingDraft]]:
    generated = GeneratedSchema(
        schema_type=schema_type,
        eligibility=type("E", (), {"status": "eligible"})(),
        json_ld=json_ld,
    )
    status, _, _, findings = validate_schema(generated, content, brief)
    return status, findings


def _validate_syntax(doc: dict[str, Any]) -> list[SchemaFindingDraft]:
    findings: list[SchemaFindingDraft] = []
    try:
        json.dumps(doc)
    except (TypeError, ValueError) as exc:
        findings.append(
            _finding(
                "invalid_json",
                "syntax",
                "critical",
                "ERROR",
                "Invalid JSON",
                str(exc),
                "JSON-LD must serialize to valid JSON.",
            )
        )
    return findings


def _validate_structure(doc: dict[str, Any]) -> list[SchemaFindingDraft]:
    findings: list[SchemaFindingDraft] = []
    ctx = doc.get("@context")
    if ctx != SCHEMA_CONTEXT:
        findings.append(
            _finding(
                "invalid_context",
                "structure",
                "high",
                "ERROR",
                "Invalid @context",
                f"Expected {SCHEMA_CONTEXT}, got {ctx!r}.",
                "@context must be https://schema.org.",
                "@context",
            )
        )
    schema_type = doc.get("@type")
    if not schema_type:
        findings.append(
            _finding(
                "missing_type",
                "structure",
                "high",
                "ERROR",
                "Missing @type",
                "JSON-LD must include @type.",
                "Add a supported @type value.",
                "@type",
            )
        )
    elif schema_type not in SUPPORTED_SCHEMA_TYPES:
        findings.append(
            _finding(
                "unsupported_type",
                "structure",
                "high",
                "ERROR",
                "Unsupported @type",
                f"{schema_type} is not supported by M9.11.",
                "Use a supported schema.org type.",
                "@type",
            )
        )

    _check_required_properties(doc, schema_type, findings)
    _check_recommended_properties(doc, schema_type, findings)
    _check_urls(doc, findings)
    return findings


def _check_required_properties(doc: dict, schema_type: str | None, findings: list[SchemaFindingDraft]) -> None:
    required_map = {
        "Article": ["headline"],
        "BlogPosting": ["headline"],
        "WebPage": ["name"],
        "FAQPage": ["mainEntity"],
        "HowTo": ["name", "step"],
        "Organization": ["name"],
        "BreadcrumbList": ["itemListElement"],
    }
    for prop in required_map.get(str(schema_type), []):
        if prop not in doc or doc[prop] in (None, "", []):
            findings.append(
                _finding(
                    "missing_required_property",
                    "required_property",
                    "high",
                    "ERROR",
                    f"Missing required property: {prop}",
                    f"{schema_type} requires {prop}.",
                    f"Provide {prop} from verified content.",
                    prop,
                )
            )


def _check_recommended_properties(doc: dict, schema_type: str | None, findings: list[SchemaFindingDraft]) -> None:
    recommended_map = {
        "Article": ["description"],
        "BlogPosting": ["description"],
        "WebPage": ["description"],
        "HowTo": ["description"],
    }
    for prop in recommended_map.get(str(schema_type), []):
        if prop not in doc or doc[prop] in (None, ""):
            findings.append(
                _finding(
                    "missing_recommended_property",
                    "recommended_property",
                    "low",
                    "WARNING",
                    f"Missing recommended property: {prop}",
                    f"{schema_type} recommends {prop}.",
                    "Add when verified content supports it.",
                    prop,
                )
            )


def _check_urls(doc: dict, findings: list[SchemaFindingDraft], path: str = "") -> None:
    for key, value in doc.items():
        current = f"{path}.{key}" if path else key
        if isinstance(value, str) and (key.endswith("url") or key in {"item", "@id", "url"}):
            if not _valid_url(value):
                findings.append(
                    _finding(
                        "invalid_url",
                        "url",
                        "medium",
                        "ERROR",
                        f"Invalid URL at {current}",
                        f"Value {value!r} is not a valid http(s) URL.",
                        "Use verified URLs only.",
                        current,
                    )
                )
        elif isinstance(value, dict):
            _check_urls(value, findings, current)
        elif isinstance(value, list):
            for idx, item in enumerate(value):
                if isinstance(item, dict):
                    _check_urls(item, findings, f"{current}[{idx}]")


def _validate_no_fabricated_properties(doc: dict[str, Any]) -> list[SchemaFindingDraft]:
    findings: list[SchemaFindingDraft] = []
    for key in _flatten_keys(doc):
        if key in FABRICATED_PROPERTY_DENYLIST:
            findings.append(
                _finding(
                    "fabricated_property",
                    "evidence",
                    "critical",
                    "ERROR",
                    f"Fabricated property not allowed: {key}",
                    f"Property {key} requires verified evidence and must not be invented.",
                    "Remove or supply verified evidence.",
                    key,
                )
            )
    return findings


def _validate_content_consistency(
    doc: dict[str, Any],
    schema_type: str,
    content: SeoGeneratedContent,
    brief: SeoContentBrief,
) -> list[SchemaFindingDraft]:
    findings: list[SchemaFindingDraft] = []
    headline = doc.get("headline") or doc.get("name")
    if headline and content.title and headline.strip() != content.title.strip():
        findings.append(
            _finding(
                "headline_mismatch",
                "content_consistency",
                "high",
                "ERROR",
                "Headline does not match content title",
                f"Schema headline {headline!r} != content title {content.title!r}.",
                "Headline must derive from generated content title.",
                "headline",
            )
        )
    desc = doc.get("description")
    if desc and content.meta_description and desc.strip() != content.meta_description.strip():
        if content.meta_description not in desc and desc not in content.meta_description:
            findings.append(
                _finding(
                    "description_mismatch",
                    "content_consistency",
                    "medium",
                    "WARNING",
                    "Description may not match meta description",
                    "Schema description should align with meta_description or content excerpt.",
                    "Align description with verified meta_description.",
                    "description",
                )
            )
    if schema_type == "FAQPage":
        entities = doc.get("mainEntity") or []
        if len(entities) < 2:
            findings.append(
                _finding(
                    "faq_insufficient_pairs",
                    "content_consistency",
                    "high",
                    "ERROR",
                    "FAQPage requires at least two Q&A pairs",
                    "Insufficient mainEntity entries.",
                    "Only include genuine FAQ pairs from content.",
                    "mainEntity",
                )
            )
    if schema_type == "HowTo":
        steps = doc.get("step") or []
        sections = [s for s in (content.structured_sections or []) if str(s.get("content", "")).strip()]
        if len(steps) < len(sections):
            findings.append(
                _finding(
                    "howto_step_mismatch",
                    "content_consistency",
                    "medium",
                    "WARNING",
                    "HowTo steps may not cover all sections",
                    "Step count differs from structured sections.",
                    "Ensure each step maps to actual content sections.",
                    "step",
                )
            )
    return findings


def _flatten_keys(obj: Any, prefix: str = "") -> list[str]:
    keys: list[str] = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k.startswith("@"):
                continue
            keys.append(k)
            keys.extend(_flatten_keys(v, k))
    elif isinstance(obj, list):
        for item in obj:
            keys.extend(_flatten_keys(item, prefix))
    return keys


def _valid_url(value: str) -> bool:
    parsed = urlparse(value)
    return parsed.scheme in {"http", "https"} and bool(parsed.hostname)


def _finding(
    finding_type: str,
    category: str,
    severity: str,
    level: str,
    title: str,
    summary: str,
    rationale: str,
    property_path: str | None = None,
) -> SchemaFindingDraft:
    return SchemaFindingDraft(
        finding_type=finding_type,
        category=category,
        severity=severity,
        level=level,
        title=title,
        summary=summary,
        rationale=rationale,
        property_path=property_path,
        evidence_refs=[{"source": "schema_validation", "id": finding_type, "reason": category}],
    )
