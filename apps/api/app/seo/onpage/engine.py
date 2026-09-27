"""Deterministic on-page SEO analysis engine (M9.10)."""

from __future__ import annotations

import re
from collections import Counter
from typing import Any

from app.models.seo_content_brief import SeoContentBrief
from app.models.seo_generated_content import SeoGeneratedContent
from app.seo.onpage.thresholds import (
    KEYWORD_STUFFING_RATIO,
    LONG_PARAGRAPH_WORDS,
    META_DESC_MAX,
    META_TITLE_MAX,
    META_TITLE_MIN,
    MIN_WORD_COUNT_GUIDE,
    UNAVAILABLE_KEYWORD,
)
from app.seo.onpage.types import OnPageFindingDraft


def analyze_on_page(content: SeoGeneratedContent, brief: SeoContentBrief) -> list[OnPageFindingDraft]:
    findings: list[OnPageFindingDraft] = []
    pk = (content.primary_keyword or "").strip()
    body = _full_text(content)
    sections = content.structured_sections or []

    findings.extend(_check_title_meta(content, pk))
    findings.extend(_check_headings(content, sections, brief))
    findings.extend(_check_keywords(content, pk, body))
    findings.extend(_check_topic_and_intent(content, brief, body))
    findings.extend(_check_outline_and_questions(content, brief, sections, body))
    findings.extend(_check_entities(brief, body))
    findings.extend(_check_internal_links(content, brief, body))
    findings.extend(_check_content_quality(content, sections, body))
    findings.extend(_check_target_url(content))

    return findings[:100]


def _ref(source: str, reason: str, ref_id: str | None = None) -> list[dict[str, Any]]:
    return [{"source": source, "id": ref_id or "n/a", "reason": reason}]


def _full_text(content: SeoGeneratedContent) -> str:
    parts = [content.title, content.content, content.meta_title, content.meta_description]
    return " ".join(p for p in parts if p).lower()


def _word_count(text: str) -> int:
    return len(re.findall(r"\b\w+\b", text or ""))


def _check_title_meta(content: SeoGeneratedContent, pk: str) -> list[OnPageFindingDraft]:
    findings: list[OnPageFindingDraft] = []
    mt = content.meta_title or ""
    md = content.meta_description or ""

    if not mt.strip():
        findings.append(
            OnPageFindingDraft(
                finding_type="meta_title_missing",
                category="metadata",
                severity="high",
                priority="high",
                title="Meta title missing",
                summary="Generated content has no meta title.",
                rationale="Meta titles help search engines understand page topic.",
                current_value="",
                expected_value="30-70 character meta title",
                recommendation="Add a concise meta title aligned with the primary keyword.",
                evidence_refs=_ref("generated_content", "meta_title field empty", str(content.id)),
                affected_element="meta_title",
            )
        )
    else:
        if len(mt) > META_TITLE_MAX:
            findings.append(
                OnPageFindingDraft(
                    finding_type="meta_title_too_long",
                    category="metadata",
                    severity="medium",
                    priority="medium",
                    title="Meta title too long",
                    summary=f"Meta title is {len(mt)} characters.",
                    rationale=f"Meta titles over {META_TITLE_MAX} characters may truncate in SERPs.",
                    current_value=mt,
                    expected_value=f"<= {META_TITLE_MAX} characters",
                    recommendation="Shorten the meta title while keeping the primary topic.",
                    evidence_refs=_ref("generated_content", "meta_title length", str(content.id)),
                    affected_element="meta_title",
                )
            )
        if pk and pk != UNAVAILABLE_KEYWORD and pk.lower() not in mt.lower():
            findings.append(
                OnPageFindingDraft(
                    finding_type="meta_title_missing_keyword",
                    category="keyword",
                    severity="medium",
                    priority="medium",
                    title="Primary keyword absent from meta title",
                    summary="The brief primary keyword is not in the meta title.",
                    rationale="Including the primary keyword in the meta title can improve topical clarity.",
                    current_value=mt,
                    expected_value=f"Include '{pk}' naturally",
                    recommendation="Revise meta title to include the primary keyword naturally.",
                    evidence_refs=_ref("content_brief", "primary_keyword from brief", str(content.content_brief_id)),
                    affected_element="meta_title",
                )
            )

    if not md.strip():
        findings.append(
            OnPageFindingDraft(
                finding_type="meta_description_missing",
                category="metadata",
                severity="medium",
                priority="medium",
                title="Meta description missing",
                summary="Generated content has no meta description.",
                rationale="Meta descriptions influence click-through from search results.",
                current_value="",
                expected_value=f"<= {META_DESC_MAX} character description",
                recommendation="Add a compelling meta description.",
                evidence_refs=_ref("generated_content", "meta_description empty", str(content.id)),
                affected_element="meta_description",
            )
        )
    elif len(md) > META_DESC_MAX:
        findings.append(
            OnPageFindingDraft(
                finding_type="meta_description_too_long",
                category="metadata",
                severity="low",
                priority="low",
                title="Meta description too long",
                summary=f"Meta description is {len(md)} characters.",
                rationale=f"Descriptions over {META_DESC_MAX} characters may truncate.",
                current_value=md[:80] + "...",
                expected_value=f"<= {META_DESC_MAX} characters",
                recommendation="Shorten the meta description.",
                evidence_refs=_ref("generated_content", "meta_description length", str(content.id)),
                affected_element="meta_description",
            )
        )

    if not (content.title or "").strip():
        findings.append(
            OnPageFindingDraft(
                finding_type="title_missing",
                category="title",
                severity="high",
                priority="high",
                title="Content title missing",
                summary="The generated content has no title.",
                rationale="A clear title is required for on-page SEO and user orientation.",
                recommendation="Add a descriptive title.",
                evidence_refs=_ref("generated_content", "title empty", str(content.id)),
                affected_element="title",
            )
        )

    return findings


def _check_headings(content: SeoGeneratedContent, sections: list, brief: SeoContentBrief) -> list[OnPageFindingDraft]:
    findings: list[OnPageFindingDraft] = []
    if not sections:
        findings.append(
            OnPageFindingDraft(
                finding_type="no_sections",
                category="headings",
                severity="high",
                priority="high",
                title="No structured sections",
                summary="Generated content has no H2 sections.",
                rationale="Structured headings improve readability and topical coverage.",
                recommendation="Add H2 sections following the content brief outline.",
                evidence_refs=_ref("generated_content", "structured_sections empty", str(content.id)),
                affected_element="sections",
            )
        )
        return findings

    headings = [str(s.get("heading", "")).strip() for s in sections if s.get("heading")]
    if len(headings) != len(set(h.lower() for h in headings if h)):
        findings.append(
            OnPageFindingDraft(
                finding_type="duplicate_headings",
                category="headings",
                severity="low",
                priority="low",
                title="Duplicate section headings",
                summary="Multiple sections share the same heading text.",
                rationale="Duplicate headings reduce clarity for readers and crawlers.",
                recommendation="Use distinct headings for each section.",
                evidence_refs=_ref("generated_content", "duplicate headings", str(content.id)),
                affected_element="sections",
            )
        )

    for section in sections:
        heading = str(section.get("heading", "")).strip()
        body = str(section.get("content", "")).strip()
        if heading and not body:
            findings.append(
                OnPageFindingDraft(
                    finding_type="empty_section",
                    category="content_structure",
                    severity="medium",
                    priority="medium",
                    title=f"Empty section: {heading}",
                    summary="A section heading has no body content.",
                    rationale="Empty sections provide no value to readers.",
                    current_value="",
                    recommendation="Add content to this section or remove the heading.",
                    evidence_refs=_ref("generated_content", "empty section body", str(content.id)),
                    affected_section=heading,
                    affected_element="section_content",
                )
            )

    outline = brief.outline or content.outline_used or []
    outline_heads = [str(o.get("heading", "")).lower() for o in outline if o.get("heading")]
    section_heads = [h.lower() for h in headings]
    if outline_heads:
        matched = sum(1 for oh in outline_heads if any(oh in sh or sh in oh for sh in section_heads))
        if matched < max(1, len(outline_heads) // 2):
            findings.append(
                OnPageFindingDraft(
                    finding_type="outline_not_followed",
                    category="content_structure",
                    severity="medium",
                    priority="medium",
                    title="Outline not sufficiently followed",
                    summary="Generated sections do not align with the content brief outline.",
                    rationale="The brief outline defines required topical structure.",
                    recommendation="Add or rename sections to match the brief outline.",
                    evidence_refs=_ref("content_brief", "outline from brief", str(brief.id)),
                    affected_element="sections",
                )
            )

    return findings


def _check_keywords(content: SeoGeneratedContent, pk: str, body: str) -> list[OnPageFindingDraft]:
    findings: list[OnPageFindingDraft] = []
    if not pk or pk == UNAVAILABLE_KEYWORD:
        return findings

    pk_lower = pk.lower()
    if pk_lower not in (content.title or "").lower():
        findings.append(
            OnPageFindingDraft(
                finding_type="title_missing_primary_keyword",
                category="keyword",
                severity="medium",
                priority="medium",
                title="Primary keyword not in title",
                summary="The primary keyword from the brief is not in the content title.",
                rationale="Title-keyword alignment supports topical relevance.",
                current_value=content.title,
                recommendation=f"Include '{pk}' naturally in the title.",
                evidence_refs=_ref("content_brief", "primary_keyword", str(content.content_brief_id)),
                affected_element="title",
            )
        )

    count = body.count(pk_lower)
    total_words = max(content.word_count or _word_count(body), 1)
    ratio = count / total_words
    if ratio > KEYWORD_STUFFING_RATIO:
        findings.append(
            OnPageFindingDraft(
                finding_type="keyword_stuffing",
                category="keyword",
                severity="medium",
                priority="medium",
                title="Possible keyword stuffing",
                summary=f"Primary keyword appears {count} times in ~{total_words} words.",
                rationale="Excessive keyword repetition can harm readability.",
                current_value=str(count),
                expected_value="Natural keyword usage",
                recommendation="Reduce repetitive keyword usage; use synonyms where appropriate.",
                evidence_refs=_ref("generated_content", "keyword frequency", str(content.id)),
                affected_element="body",
            )
        )

    secondary = content.secondary_keywords or []
    if secondary:
        missing = [k for k in secondary if k.lower() not in body]
        if len(missing) == len(secondary):
            findings.append(
                OnPageFindingDraft(
                    finding_type="secondary_keywords_missing",
                    category="keyword",
                    severity="info",
                    priority="low",
                    title="Secondary keywords not used",
                    summary="None of the brief secondary keywords appear in the content.",
                    rationale="Secondary keywords can broaden topical coverage when used naturally.",
                    recommendation="Consider incorporating secondary keywords where natural.",
                    evidence_refs=_ref("content_brief", "secondary_keywords", str(content.content_brief_id)),
                    affected_element="body",
                )
            )

    return findings


def _check_topic_and_intent(content: SeoGeneratedContent, brief: SeoContentBrief, body: str) -> list[OnPageFindingDraft]:
    findings: list[OnPageFindingDraft] = []
    topic = content.target_topic or brief.target_topic
    if topic and topic.lower() not in body:
        findings.append(
            OnPageFindingDraft(
                finding_type="topic_not_covered",
                category="topic",
                severity="medium",
                priority="medium",
                title="Target topic not represented",
                summary="The target topic from the brief is not clearly present in the content.",
                rationale="Content should reflect the brief's target topic.",
                current_value=topic,
                recommendation="Ensure the target topic is addressed in headings or body.",
                evidence_refs=_ref("content_brief", "target_topic", str(brief.id)),
                affected_element="body",
            )
        )

    intent = brief.search_intent or {}
    intent_type = str(intent.get("type", "unknown"))
    if intent_type != "unknown" and "interpretation" in str(intent.get("interpretation_note", "")).lower():
        findings.append(
            OnPageFindingDraft(
                finding_type="search_intent_note",
                category="search_intent",
                severity="info",
                priority="low",
                title="Search intent is AI-interpreted",
                summary=f"Brief search intent ({intent_type}) is an interpretation, not verified by Google.",
                rationale="Intent alignment should be reviewed manually.",
                evidence_refs=_ref("content_brief", "search_intent interpretation", str(brief.id)),
                affected_element="search_intent",
                status="open",
            )
        )

    return findings


def _check_outline_and_questions(
    content: SeoGeneratedContent, brief: SeoContentBrief, sections: list, body: str
) -> list[OnPageFindingDraft]:
    findings: list[OnPageFindingDraft] = []
    questions = brief.questions_to_answer or []
    if questions:
        answered = 0
        for q in questions:
            tokens = [t for t in re.findall(r"[a-z0-9]+", q.lower()) if len(t) > 3]
            if tokens and any(t in body for t in tokens[:3]):
                answered += 1
        if answered == 0:
            findings.append(
                OnPageFindingDraft(
                    finding_type="questions_unanswered",
                    category="questions",
                    severity="medium",
                    priority="medium",
                    title="Brief questions not addressed",
                    summary="Required questions from the brief are not clearly answered.",
                    rationale="The content brief specifies questions the content should address.",
                    recommendation="Add sections that answer the brief's questions.",
                    evidence_refs=_ref("content_brief", "questions_to_answer", str(brief.id)),
                    affected_element="body",
                )
            )
    return findings


def _check_entities(brief: SeoContentBrief, body: str) -> list[OnPageFindingDraft]:
    entities = brief.entities_to_cover or []
    if not entities:
        return []
    missing = [e for e in entities if e.lower() not in body]
    if missing == entities:
        return [
            OnPageFindingDraft(
                finding_type="entities_missing",
                category="entities",
                severity="low",
                priority="low",
                title="Required entities not covered",
                summary="Entities from the brief are not mentioned in the content.",
                rationale="Brief entities define concepts the content should cover.",
                recommendation="Mention required entities where relevant.",
                evidence_refs=_ref("content_brief", "entities_to_cover", str(brief.id)),
                affected_element="body",
            )
        ]
    return []


def _check_internal_links(content: SeoGeneratedContent, brief: SeoContentBrief, body: str) -> list[OnPageFindingDraft]:
    findings: list[OnPageFindingDraft] = []
    approved = set()
    for item in content.internal_link_targets or []:
        if isinstance(item, dict):
            approved.add(item.get("url", ""))
        elif isinstance(item, str):
            approved.add(item)
    for url in brief.internal_link_targets or []:
        approved.add(url)

    approved = {u for u in approved if u}

    found_in_body = {u for u in approved if u.lower() in body}
    missing = approved - found_in_body
    if approved and missing:
        findings.append(
            OnPageFindingDraft(
                finding_type="internal_links_missing",
                category="internal_links",
                severity="medium",
                priority="medium",
                title="Approved internal links not used",
                summary=f"{len(missing)} approved internal link(s) are not present in the content.",
                rationale="The brief approved specific internal link targets.",
                current_value=", ".join(sorted(missing)[:3]),
                recommendation="Add approved internal links with descriptive anchor text.",
                evidence_refs=_ref("content_brief", "internal_link_targets", str(brief.id)),
                affected_element="internal_links",
            )
        )

    url_pattern = re.compile(r"https?://[^\s)\]]+")
    for url in url_pattern.findall(content.content or ""):
        if approved and url not in approved:
            findings.append(
                OnPageFindingDraft(
                    finding_type="unapproved_internal_url",
                    category="internal_links",
                    severity="high",
                    priority="high",
                    title="Unapproved URL in content",
                    summary="Content contains a URL not in approved internal link targets.",
                    rationale="Only brief-approved URLs should be used for internal links.",
                    current_value=url,
                    recommendation="Remove or replace with an approved internal link URL.",
                    evidence_refs=_ref("generated_content", "unapproved URL", str(content.id)),
                    affected_element="internal_links",
                )
            )
            break

    return findings


def _check_content_quality(content: SeoGeneratedContent, sections: list, body: str) -> list[OnPageFindingDraft]:
    findings: list[OnPageFindingDraft] = []
    wc = content.word_count or _word_count(body)
    if wc < MIN_WORD_COUNT_GUIDE and content.content_type in ("guide", "informational_article", "blog_article"):
        findings.append(
            OnPageFindingDraft(
                finding_type="content_thin",
                category="content_depth",
                severity="medium",
                priority="medium",
                title="Content may be too thin",
                summary=f"Word count ({wc}) is below typical guide depth.",
                rationale="Thin content may not fully address the brief's topic.",
                current_value=str(wc),
                recommendation="Expand sections with more detail aligned to the brief.",
                evidence_refs=_ref("generated_content", "word_count", str(content.id)),
                affected_element="body",
            )
        )

    for section in sections:
        text = str(section.get("content", ""))
        if _word_count(text) > LONG_PARAGRAPH_WORDS:
            findings.append(
                OnPageFindingDraft(
                    finding_type="long_paragraph",
                    category="readability",
                    severity="low",
                    priority="low",
                    title=f"Long section: {section.get('heading', 'section')}",
                    summary="A section exceeds recommended paragraph length.",
                    rationale="Shorter paragraphs improve readability.",
                    affected_section=str(section.get("heading", "")),
                    recommendation="Break long sections into shorter paragraphs.",
                    evidence_refs=_ref("generated_content", "section length", str(content.id)),
                    affected_element="section_content",
                )
            )
            break

    words = re.findall(r"\b\w+\b", body)
    if words:
        bigrams = [" ".join(words[i : i + 2]) for i in range(len(words) - 1)]
        repeated = [p for p, c in Counter(bigrams).items() if c >= 4 and len(p) > 10]
        if repeated:
            findings.append(
                OnPageFindingDraft(
                    finding_type="repetitive_phrasing",
                    category="duplicate_content",
                    severity="low",
                    priority="low",
                    title="Repetitive phrasing detected",
                    summary="Some phrases repeat frequently in the content.",
                    rationale="Repetition can reduce readability.",
                    current_value=repeated[0][:60],
                    recommendation="Vary wording while preserving meaning.",
                    evidence_refs=_ref("generated_content", "phrase repetition", str(content.id)),
                    affected_element="body",
                )
            )

    return findings


def _check_target_url(content: SeoGeneratedContent) -> list[OnPageFindingDraft]:
    if not content.target_url:
        return []
    if not content.target_url.startswith(("http://", "https://")):
        return [
            OnPageFindingDraft(
                finding_type="invalid_target_url",
                category="metadata",
                severity="medium",
                priority="low",
                title="Invalid target URL format",
                summary="Target URL is not a valid HTTP(S) URL.",
                rationale="Target URLs should use HTTP or HTTPS schemes from verified evidence.",
                current_value=content.target_url,
                recommendation="Use a valid URL from crawl evidence.",
                evidence_refs=_ref("generated_content", "target_url", str(content.id)),
                affected_element="target_url",
            )
        ]
    return []
