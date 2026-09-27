"""Deterministic JSON-LD schema generator (M9.11)."""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlparse

from app.models.seo_content_brief import SeoContentBrief
from app.models.seo_generated_content import SeoGeneratedContent
from app.seo.schema.eligibility import _question_tokens_in_body, _site_root, _valid_http_url
from app.seo.schema.thresholds import SCHEMA_CONTEXT
from app.seo.schema.types import EligibilityResult, GeneratedSchema


def generate_schemas(
    content: SeoGeneratedContent,
    brief: SeoContentBrief,
    eligibility_results: list[EligibilityResult],
) -> list[GeneratedSchema]:
    generated: list[GeneratedSchema] = []
    for elig in eligibility_results:
        if elig.status != "eligible":
            generated.append(
                GeneratedSchema(
                    schema_type=elig.schema_type,
                    eligibility=elig,
                    json_ld=None,
                    evidence_refs=_base_evidence(content, brief),
                )
            )
            continue
        builder = _BUILDERS.get(elig.schema_type)
        if not builder:
            generated.append(
                GeneratedSchema(
                    schema_type=elig.schema_type,
                    eligibility=elig,
                    json_ld=None,
                    evidence_refs=_base_evidence(content, brief),
                )
            )
            continue
        json_ld, source_fields = builder(content, brief)
        generated.append(
            GeneratedSchema(
                schema_type=elig.schema_type,
                eligibility=elig,
                json_ld=json_ld,
                source_fields=source_fields,
                evidence_refs=_base_evidence(content, brief),
            )
        )
    return generated


def _base_evidence(content: SeoGeneratedContent, brief: SeoContentBrief) -> list[dict[str, Any]]:
    refs: list[dict[str, Any]] = [
        {"source": "generated_content", "id": str(content.id), "reason": "primary content source"},
        {"source": "content_brief", "id": str(brief.id), "reason": "brief context"},
    ]
    for ref in (content.evidence_refs or [])[:5]:
        if isinstance(ref, dict):
            refs.append(ref)
    return refs


def _article_json(content: SeoGeneratedContent, brief: SeoContentBrief, schema_type: str) -> tuple[dict, dict]:
    source: dict[str, str] = {"headline": "generated_content.title", "description": "generated_content.meta_description"}
    doc: dict[str, Any] = {
        "@context": SCHEMA_CONTEXT,
        "@type": schema_type,
        "headline": content.title,
        "description": content.meta_description or _excerpt(content.content),
    }
    target = content.target_url or brief.target_url
    if target and _valid_http_url(target):
        doc["mainEntityOfPage"] = {"@type": "WebPage", "@id": target}
        source["mainEntityOfPage"] = "generated_content.target_url"
    return doc, source


def _webpage_json(content: SeoGeneratedContent, brief: SeoContentBrief) -> tuple[dict, dict]:
    source: dict[str, str] = {"name": "generated_content.title", "description": "generated_content.meta_description"}
    doc: dict[str, Any] = {
        "@context": SCHEMA_CONTEXT,
        "@type": "WebPage",
        "name": content.title,
        "description": content.meta_description or _excerpt(content.content),
    }
    target = content.target_url or brief.target_url
    if target and _valid_http_url(target):
        doc["url"] = target
        source["url"] = "generated_content.target_url"
    return doc, source


def _faq_json(content: SeoGeneratedContent, brief: SeoContentBrief) -> tuple[dict, dict]:
    body = content.content or ""
    entities: list[dict[str, Any]] = []
    for question in brief.questions_to_answer or []:
        if not _question_tokens_in_body(question, body.lower()):
            continue
        answer = _answer_for_question(question, content)
        if not answer:
            continue
        entities.append(
            {
                "@type": "Question",
                "name": question,
                "acceptedAnswer": {"@type": "Answer", "text": answer},
            }
        )
    doc = {"@context": SCHEMA_CONTEXT, "@type": "FAQPage", "mainEntity": entities}
    return doc, {"mainEntity": "content_brief.questions_to_answer + generated_content.content"}


def _howto_json(content: SeoGeneratedContent, brief: SeoContentBrief) -> tuple[dict, dict]:
    steps: list[dict[str, Any]] = []
    for idx, section in enumerate(content.structured_sections or [], start=1):
        heading = str(section.get("heading", "")).strip()
        text = str(section.get("content", "")).strip()
        if not heading or not text:
            continue
        steps.append({"@type": "HowToStep", "position": idx, "name": heading, "text": text})
    doc: dict[str, Any] = {
        "@context": SCHEMA_CONTEXT,
        "@type": "HowTo",
        "name": content.title,
        "description": content.meta_description or _excerpt(content.content),
        "step": steps,
    }
    return doc, {"step": "generated_content.structured_sections"}


def _organization_json(content: SeoGeneratedContent, brief: SeoContentBrief) -> tuple[dict, dict]:
    snapshot = brief.context_snapshot or content.brief_snapshot or {}
    name = snapshot.get("business_name") or snapshot.get("organization_name") or brief.title
    url = content.target_url or brief.target_url or snapshot.get("site_url")
    doc: dict[str, Any] = {"@context": SCHEMA_CONTEXT, "@type": "Organization", "name": name}
    source = {"name": "content_brief.context_snapshot.business_name"}
    if url and _valid_http_url(url):
        doc["url"] = _site_root(url) or url
        source["url"] = "generated_content.target_url"
    return doc, source


def _breadcrumb_json(content: SeoGeneratedContent, brief: SeoContentBrief) -> tuple[dict, dict]:
    target = content.target_url or brief.target_url or ""
    root = _site_root(target) or target
    items = [
        {"@type": "ListItem", "position": 1, "name": "Home", "item": root},
        {"@type": "ListItem", "position": 2, "name": content.title, "item": target},
    ]
    doc = {"@context": SCHEMA_CONTEXT, "@type": "BreadcrumbList", "itemListElement": items}
    return doc, {"itemListElement": "derived from target_url and content.title"}


def _excerpt(text: str, limit: int = 160) -> str:
    cleaned = re.sub(r"\s+", " ", (text or "").strip())
    return cleaned[:limit]


def _answer_for_question(question: str, content: SeoGeneratedContent) -> str | None:
    body = content.content or ""
    for section in content.structured_sections or []:
        heading = str(section.get("heading", "")).lower()
        if any(t in heading for t in re.findall(r"[a-z0-9]+", question.lower()) if len(t) > 3):
            text = str(section.get("content", "")).strip()
            if text:
                return text[:2000]
    tokens = [t for t in re.findall(r"[a-z0-9]+", question.lower()) if len(t) > 3]
    if tokens and any(t in body.lower() for t in tokens):
        idx = body.lower().find(tokens[0])
        return body[idx : idx + 500].strip()
    return None


_BUILDERS = {
    "Article": lambda c, b: _article_json(c, b, "Article"),
    "BlogPosting": lambda c, b: _article_json(c, b, "BlogPosting"),
    "WebPage": _webpage_json,
    "FAQPage": _faq_json,
    "HowTo": _howto_json,
    "Organization": _organization_json,
    "BreadcrumbList": _breadcrumb_json,
}
