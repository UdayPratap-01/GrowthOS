"""Deterministic schema eligibility engine (M9.11)."""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlparse

from app.models.seo_content_brief import SeoContentBrief
from app.models.seo_generated_content import SeoGeneratedContent
from app.seo.schema.types import EligibilityResult


def evaluate_eligibility(content: SeoGeneratedContent, brief: SeoContentBrief) -> list[EligibilityResult]:
    results: list[EligibilityResult] = []
    for schema_type in (
        "Article",
        "BlogPosting",
        "NewsArticle",
        "WebPage",
        "FAQPage",
        "HowTo",
        "Service",
        "Product",
        "LocalBusiness",
        "Organization",
        "BreadcrumbList",
    ):
        results.append(_check_type(content, brief, schema_type))
    return results


def _check_type(content: SeoGeneratedContent, brief: SeoContentBrief, schema_type: str) -> EligibilityResult:
    if schema_type == "Article":
        return _article_eligibility(content, brief, "Article")
    if schema_type == "BlogPosting":
        return _article_eligibility(content, brief, "BlogPosting", require_blog=True)
    if schema_type == "NewsArticle":
        return EligibilityResult("NewsArticle", "ineligible", ["NewsArticle requires verified news content; not applicable."])
    if schema_type == "WebPage":
        if content.title and (content.meta_description or content.content):
            return EligibilityResult("WebPage", "eligible", ["Title and page content available."])
        return EligibilityResult("WebPage", "insufficient_data", ["Missing title or page content."])
    if schema_type == "FAQPage":
        return _faq_eligibility(content, brief)
    if schema_type == "HowTo":
        return _howto_eligibility(content, brief)
    if schema_type == "Service":
        if content.content_type == "service_content":
            return EligibilityResult("Service", "insufficient_data", ["Service schema requires verified service business data."])
        return EligibilityResult("Service", "ineligible", ["Content is not service-focused."])
    if schema_type == "Product":
        return EligibilityResult("Product", "insufficient_data", ["Product schema requires verified product evidence (price, SKU, etc.)."])
    if schema_type == "LocalBusiness":
        return EligibilityResult("LocalBusiness", "insufficient_data", ["LocalBusiness requires verified address, phone, and hours."])
    if schema_type == "Organization":
        return _organization_eligibility(content, brief)
    if schema_type == "BreadcrumbList":
        return _breadcrumb_eligibility(content, brief)
    return EligibilityResult(schema_type, "ineligible", ["Unsupported schema type."])


def _article_eligibility(
    content: SeoGeneratedContent, brief: SeoContentBrief, schema_type: str, *, require_blog: bool = False
) -> EligibilityResult:
    if require_blog and content.content_type not in ("blog_article",):
        return EligibilityResult(schema_type, "ineligible", ["Content type is not a blog article."])
    if not require_blog and content.content_type == "blog_article":
        return EligibilityResult(schema_type, "ineligible", ["Use BlogPosting for blog articles."])
    if content.content_type not in ("guide", "informational_article", "blog_article", "comparison", "content_refresh"):
        if not require_blog:
            return EligibilityResult(schema_type, "ineligible", [f"Content type {content.content_type} is not article-like."])
    if not content.title:
        return EligibilityResult(schema_type, "insufficient_data", ["Missing title for headline."])
    if not (content.meta_description or content.content):
        return EligibilityResult(schema_type, "insufficient_data", ["Missing description or body content."])
    return EligibilityResult(schema_type, "eligible", ["Article-like content with title and description available."])


def _faq_eligibility(content: SeoGeneratedContent, brief: SeoContentBrief) -> EligibilityResult:
    questions = brief.questions_to_answer or []
    if len(questions) < 2:
        return EligibilityResult("FAQPage", "ineligible", ["Fewer than two brief questions; FAQPage not applicable."])
    body = (content.content or "").lower()
    answered = sum(1 for q in questions if _question_tokens_in_body(q, body))
    if answered < 2:
        return EligibilityResult("FAQPage", "insufficient_data", ["Brief questions are not answered in generated content."])
    return EligibilityResult("FAQPage", "eligible", [f"{answered} question(s) have supporting content."])


def _howto_eligibility(content: SeoGeneratedContent, brief: SeoContentBrief) -> EligibilityResult:
    sections = content.structured_sections or []
    if content.content_type not in ("guide",) or len(sections) < 2:
        return EligibilityResult("HowTo", "ineligible", ["HowTo requires a multi-step guide with sequential sections."])
    non_empty = [s for s in sections if str(s.get("content", "")).strip()]
    if len(non_empty) < 2:
        return EligibilityResult("HowTo", "insufficient_data", ["Guide sections lack step content."])
    return EligibilityResult("HowTo", "eligible", ["Guide contains sequential instructional sections."])


def _organization_eligibility(content: SeoGeneratedContent, brief: SeoContentBrief) -> EligibilityResult:
    snapshot = brief.context_snapshot or content.brief_snapshot or {}
    org_name = snapshot.get("business_name") or snapshot.get("organization_name")
    site_url = content.target_url or brief.target_url or snapshot.get("site_url")
    if org_name and site_url and _valid_http_url(site_url):
        return EligibilityResult("Organization", "eligible", ["Verified organization name and site URL available."])
    return EligibilityResult("Organization", "insufficient_data", ["Missing verified organization name or site URL."])


def _breadcrumb_eligibility(content: SeoGeneratedContent, brief: SeoContentBrief) -> EligibilityResult:
    target = content.target_url or brief.target_url
    if not target or not _valid_http_url(target):
        return EligibilityResult("BreadcrumbList", "insufficient_data", ["No verified target URL for breadcrumb item."])
    root = _site_root(target)
    if not root:
        return EligibilityResult("BreadcrumbList", "insufficient_data", ["Cannot derive site root from target URL."])
    return EligibilityResult("BreadcrumbList", "eligible", ["Target URL and site root available for breadcrumb trail."])


def _question_tokens_in_body(question: str, body: str) -> bool:
    tokens = [t for t in re.findall(r"[a-z0-9]+", question.lower()) if len(t) > 3]
    return bool(tokens) and any(t in body for t in tokens[:4])


def _valid_http_url(url: str) -> bool:
    parsed = urlparse(url)
    return parsed.scheme in {"http", "https"} and bool(parsed.hostname)


def _site_root(url: str) -> str | None:
    parsed = urlparse(url)
    if not parsed.scheme or not parsed.netloc:
        return None
    return f"{parsed.scheme}://{parsed.netloc}/"
