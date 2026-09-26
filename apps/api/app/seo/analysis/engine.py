"""Deterministic technical SEO analysis over M9.1 crawl observations."""

from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from difflib import SequenceMatcher
from typing import Any
from urllib.parse import urlparse
from uuid import UUID

from app.models.seo import SeoCrawl, SeoCrawlPage
from app.seo.analysis.thresholds import AnalysisThresholds, DEFAULT_THRESHOLDS
from app.seo.analysis.types import FindingDraft
from app.seo.normalize import is_same_site, normalize_url

DISCLAIMER = (
    "Analysis is based on observable crawler evidence only. "
    "It does not determine Google indexing, ranking, traffic, backlinks, or search volume."
)


def analyze_crawl(
    crawl: SeoCrawl,
    pages: list[SeoCrawlPage],
    *,
    thresholds: AnalysisThresholds = DEFAULT_THRESHOLDS,
) -> list[FindingDraft]:
    """Run all deterministic rules and return deduplicated findings."""
    root = normalize_url(crawl.root_url) or crawl.root_url
    include_subdomains = bool((crawl.config or {}).get("include_subdomains"))
    by_url: dict[str, SeoCrawlPage] = {p.url: p for p in pages}
    html_pages = [p for p in pages if _is_html_page(p)]
    drafts: list[FindingDraft] = []

    drafts.extend(_title_rules(html_pages, thresholds))
    drafts.extend(_meta_rules(html_pages, thresholds))
    drafts.extend(_heading_rules(html_pages))
    drafts.extend(_canonical_rules(html_pages, root, include_subdomains))
    drafts.extend(_robots_meta_rules(html_pages))
    drafts.extend(_robots_txt_rules(pages))
    drafts.extend(_sitemap_rules(pages, root, by_url))
    drafts.extend(_link_rules(pages, by_url, root, include_subdomains))
    drafts.extend(_redirect_rules(pages, root, include_subdomains, thresholds))
    drafts.extend(_url_quality_rules(pages, root))
    drafts.extend(_orphan_rules(pages, root, by_url))
    drafts.extend(_depth_rules(pages, thresholds))
    drafts.extend(_image_rules(html_pages, thresholds))
    drafts.extend(_structured_data_rules(html_pages))
    drafts.extend(_hreflang_rules(html_pages, root, include_subdomains))
    drafts.extend(_protocol_rules(pages, root))
    drafts.extend(_status_content_rules(pages))

    return _dedupe_findings(drafts)


def _dedupe_findings(drafts: list[FindingDraft]) -> list[FindingDraft]:
    seen: set[str] = set()
    out: list[FindingDraft] = []
    for draft in drafts:
        key = draft.dedupe_key()
        if key in seen:
            continue
        seen.add(key)
        out.append(draft)
    return out


def _is_html_page(page: SeoCrawlPage) -> bool:
    obs = page.observations or {}
    if obs.get("non_html"):
        return False
    if obs.get("robots_txt") or obs.get("sitemap"):
        return False
    ct = (page.content_type or "").lower()
    return not ct or "html" in ct


def _title_rules(pages: list[SeoCrawlPage], t: AnalysisThresholds) -> list[FindingDraft]:
    findings: list[FindingDraft] = []
    titles: dict[str, list[SeoCrawlPage]] = defaultdict(list)

    for page in pages:
        obs = page.observations or {}
        title = obs.get("title")
        if title is None:
            findings.append(
                FindingDraft(
                    rule_id="SEO_TITLE_MISSING",
                    category="title",
                    severity="MEDIUM",
                    title="Missing title tag",
                    description="No title element was observed on this page.",
                    evidence={"url": page.url, "observed": None},
                    url=page.url,
                    page_id=page.id,
                    recommendation="Add a descriptive, unique title tag.",
                )
            )
            continue
        title_str = str(title).strip()
        if not title_str:
            findings.append(
                FindingDraft(
                    rule_id="SEO_TITLE_EMPTY",
                    category="title",
                    severity="MEDIUM",
                    title="Empty title tag",
                    description="The title element is present but empty.",
                    evidence={"url": page.url, "observed": title_str},
                    observed_value=title_str,
                    url=page.url,
                    page_id=page.id,
                    recommendation="Add descriptive text inside the title tag.",
                )
            )
            continue
        if len(title_str) < t.title_min_chars:
            findings.append(
                FindingDraft(
                    rule_id="SEO_TITLE_SHORT",
                    category="title",
                    severity="LOW",
                    title="Unusually short title",
                    description=f"Title length ({len(title_str)} chars) is below the configured heuristic minimum ({t.title_min_chars}).",
                    evidence={"url": page.url, "length": len(title_str), "threshold": t.title_min_chars},
                    observed_value=title_str,
                    expected_or_heuristic=f">= {t.title_min_chars} characters (heuristic)",
                    url=page.url,
                    page_id=page.id,
                    recommendation="Consider expanding the title with more descriptive context.",
                )
            )
        if len(title_str) > t.title_max_chars:
            findings.append(
                FindingDraft(
                    rule_id="SEO_TITLE_LONG",
                    category="title",
                    severity="LOW",
                    title="Unusually long title",
                    description=f"Title length ({len(title_str)} chars) exceeds the configured heuristic maximum ({t.title_max_chars}).",
                    evidence={"url": page.url, "length": len(title_str), "threshold": t.title_max_chars},
                    observed_value=title_str,
                    expected_or_heuristic=f"<= {t.title_max_chars} characters (heuristic)",
                    url=page.url,
                    page_id=page.id,
                    recommendation="Consider shortening the title for clarity in search snippets.",
                )
            )
        titles[title_str.lower()].append(page)

    for title_key, group in titles.items():
        if len(group) > 1:
            urls = [p.url for p in group]
            for page in group:
                findings.append(
                    FindingDraft(
                        rule_id="SEO_TITLE_DUPLICATE",
                        category="title",
                        severity="MEDIUM",
                        title="Duplicate title tag",
                        description="The same title text appears on multiple crawled pages.",
                        evidence={"url": page.url, "title": title_key, "duplicate_urls": urls},
                        observed_value=title_key,
                        url=page.url,
                        page_id=page.id,
                        fingerprint=title_key,
                        recommendation="Use unique titles that describe each page's content.",
                    )
                )

    title_list = [(p, str((p.observations or {}).get("title") or "").strip()) for p in pages if (p.observations or {}).get("title")]
    for i, (page_a, title_a) in enumerate(title_list):
        if not title_a:
            continue
        for page_b, title_b in title_list[i + 1 :]:
            if not title_b or title_a.lower() == title_b.lower():
                continue
            ratio = SequenceMatcher(None, title_a.lower(), title_b.lower()).ratio()
            if ratio >= t.near_duplicate_title_ratio:
                findings.append(
                    FindingDraft(
                        rule_id="SEO_TITLE_NEAR_DUPLICATE",
                        category="title",
                        severity="LOW",
                        title="Near-duplicate title",
                        description="Title text is very similar to another crawled page.",
                        evidence={
                            "url": page_a.url,
                            "other_url": page_b.url,
                            "similarity_ratio": round(ratio, 3),
                            "threshold": t.near_duplicate_title_ratio,
                        },
                        observed_value=title_a,
                        url=page_a.url,
                        page_id=page_a.id,
                        fingerprint=f"{page_b.url}:{title_b.lower()}",
                        recommendation="Differentiate titles so each page is clearly distinct.",
                    )
                )
    return findings


def _meta_rules(pages: list[SeoCrawlPage], t: AnalysisThresholds) -> list[FindingDraft]:
    findings: list[FindingDraft] = []
    metas: dict[str, list[SeoCrawlPage]] = defaultdict(list)

    for page in pages:
        obs = page.observations or {}
        meta = obs.get("meta_description")
        if meta is None:
            findings.append(
                FindingDraft(
                    rule_id="SEO_META_MISSING",
                    category="meta_description",
                    severity="LOW",
                    title="Missing meta description",
                    description="No meta description element was observed.",
                    evidence={"url": page.url},
                    url=page.url,
                    page_id=page.id,
                    recommendation="Add a concise meta description summarizing the page.",
                )
            )
            continue
        meta_str = str(meta).strip()
        if not meta_str:
            findings.append(
                FindingDraft(
                    rule_id="SEO_META_EMPTY",
                    category="meta_description",
                    severity="LOW",
                    title="Empty meta description",
                    description="Meta description element is present but empty.",
                    evidence={"url": page.url},
                    url=page.url,
                    page_id=page.id,
                    recommendation="Add descriptive meta description content.",
                )
            )
            continue
        if len(meta_str) < t.meta_min_chars:
            findings.append(
                FindingDraft(
                    rule_id="SEO_META_SHORT",
                    category="meta_description",
                    severity="INFO",
                    title="Short meta description",
                    description=f"Meta description ({len(meta_str)} chars) is below heuristic minimum ({t.meta_min_chars}).",
                    evidence={"length": len(meta_str), "threshold": t.meta_min_chars},
                    observed_value=meta_str,
                    expected_or_heuristic=f">= {t.meta_min_chars} characters (heuristic)",
                    url=page.url,
                    page_id=page.id,
                )
            )
        if len(meta_str) > t.meta_max_chars:
            findings.append(
                FindingDraft(
                    rule_id="SEO_META_LONG",
                    category="meta_description",
                    severity="INFO",
                    title="Long meta description",
                    description=f"Meta description ({len(meta_str)} chars) exceeds heuristic maximum ({t.meta_max_chars}).",
                    evidence={"length": len(meta_str), "threshold": t.meta_max_chars},
                    observed_value=meta_str,
                    expected_or_heuristic=f"<= {t.meta_max_chars} characters (heuristic)",
                    url=page.url,
                    page_id=page.id,
                )
            )
        metas[meta_str.lower()].append(page)

    for meta_key, group in metas.items():
        if len(group) > 1:
            for page in group:
                findings.append(
                    FindingDraft(
                        rule_id="SEO_META_DUPLICATE",
                        category="meta_description",
                        severity="LOW",
                        title="Duplicate meta description",
                        description="The same meta description appears on multiple pages.",
                        evidence={"url": page.url, "duplicate_count": len(group)},
                        observed_value=meta_key[:200],
                        url=page.url,
                        page_id=page.id,
                        fingerprint=meta_key[:200],
                        recommendation="Write unique meta descriptions per page.",
                    )
                )
    return findings


def _heading_rules(pages: list[SeoCrawlPage]) -> list[FindingDraft]:
    findings: list[FindingDraft] = []
    for page in pages:
        obs = page.observations or {}
        h1_count = int(obs.get("h1_count") or 0)
        h2_count = int(obs.get("h2_count") or 0)
        h1_text = obs.get("h1_text") or []

        if h1_count == 0:
            findings.append(
                FindingDraft(
                    rule_id="SEO_H1_MISSING",
                    category="headings",
                    severity="MEDIUM",
                    title="Missing H1 heading",
                    description="No H1 element was observed on this page.",
                    evidence={"url": page.url, "h1_count": 0},
                    url=page.url,
                    page_id=page.id,
                    recommendation="Add a primary H1 describing the page topic.",
                )
            )
        elif h1_count > 0 and not any(str(t).strip() for t in h1_text):
            findings.append(
                FindingDraft(
                    rule_id="SEO_H1_EMPTY",
                    category="headings",
                    severity="MEDIUM",
                    title="Empty H1 heading",
                    description="H1 element(s) present but contain no visible text.",
                    evidence={"url": page.url, "h1_count": h1_count},
                    url=page.url,
                    page_id=page.id,
                )
            )
        elif h1_count > 1:
            findings.append(
                FindingDraft(
                    rule_id="SEO_H1_MULTIPLE",
                    category="headings",
                    severity="INFO",
                    title="Multiple H1 headings",
                    description=f"Observed {h1_count} H1 elements — a structural observation, not a confirmed search-engine penalty.",
                    evidence={"url": page.url, "h1_count": h1_count, "h1_text": h1_text[:5]},
                    observed_value=str(h1_count),
                    url=page.url,
                    page_id=page.id,
                    recommendation="Review whether a single primary H1 better reflects page structure.",
                )
            )
        if h2_count > 0 and h1_count == 0:
            findings.append(
                FindingDraft(
                    rule_id="SEO_HEADING_HIERARCHY",
                    category="headings",
                    severity="INFO",
                    title="H2 without H1",
                    description="Page has H2 headings but no H1 — heading level may be skipped.",
                    evidence={"url": page.url, "h1_count": h1_count, "h2_count": h2_count},
                    url=page.url,
                    page_id=page.id,
                )
            )
        repeated_h2 = [t for t, c in Counter(str(x).strip().lower() for x in (obs.get("h2_text") or []) if str(x).strip()).items() if c > 2]
        if repeated_h2:
            findings.append(
                FindingDraft(
                    rule_id="SEO_HEADING_REPEATED",
                    category="headings",
                    severity="INFO",
                    title="Repeated H2 headings",
                    description="Some H2 text repeats more than twice on this page.",
                    evidence={"url": page.url, "repeated": repeated_h2[:5]},
                    url=page.url,
                    page_id=page.id,
                    fingerprint=repeated_h2[0],
                )
            )
    return findings


def _canonical_rules(pages: list[SeoCrawlPage], root: str, include_subdomains: bool) -> list[FindingDraft]:
    findings: list[FindingDraft] = []
    for page in pages:
        obs = page.observations or {}
        canonical = obs.get("canonical")
        if not canonical:
            findings.append(
                FindingDraft(
                    rule_id="SEO_CANONICAL_MISSING",
                    category="canonical",
                    severity="INFO",
                    title="Missing canonical tag",
                    description="No canonical link element was observed.",
                    evidence={"url": page.url},
                    url=page.url,
                    page_id=page.id,
                    recommendation="Consider adding a canonical URL if duplicate variants exist.",
                )
            )
            continue
        canon_norm = normalize_url(str(canonical), base_url=page.url)
        if not canon_norm:
            findings.append(
                FindingDraft(
                    rule_id="SEO_CANONICAL_MALFORMED",
                    category="canonical",
                    severity="MEDIUM",
                    title="Malformed canonical URL",
                    description="Canonical href could not be normalized to a valid URL.",
                    evidence={"url": page.url, "canonical": canonical},
                    observed_value=str(canonical),
                    url=page.url,
                    page_id=page.id,
                )
            )
            continue
        page_norm = normalize_url(page.final_url or page.url)
        if page_norm and canon_norm != page_norm:
            severity = "INFO"
            if not is_same_site(canon_norm, root, include_subdomains=include_subdomains):
                severity = "WARNING"
            findings.append(
                FindingDraft(
                    rule_id="SEO_CANONICAL_MISMATCH",
                    category="canonical",
                    severity=severity,
                    title="Canonical URL mismatch",
                    description="Canonical URL differs from the crawled page URL.",
                    evidence={"url": page.url, "canonical": canon_norm, "page_url": page_norm},
                    observed_value=canon_norm,
                    expected_or_heuristic=page_norm,
                    url=page.url,
                    page_id=page.id,
                )
            )
    return findings


def _robots_meta_rules(pages: list[SeoCrawlPage]) -> list[FindingDraft]:
    findings: list[FindingDraft] = []
    for page in pages:
        obs = page.observations or {}
        robots = obs.get("robots_meta")
        if not robots:
            continue
        robots_lower = str(robots).lower()
        directives = [d.strip() for d in re.split(r"[,;]", robots_lower) if d.strip()]
        for directive in directives:
            if directive in {"noindex", "nofollow", "nosnippet", "noarchive", "noimageindex"}:
                findings.append(
                    FindingDraft(
                        rule_id=f"SEO_ROBOTS_META_{directive.upper()}",
                        category="robots_meta",
                        severity="INFO" if directive != "noindex" else "MEDIUM",
                        title=f"Observed robots meta directive: {directive}",
                        description=f'Page contains robots meta content including "{directive}". This is a crawl observation — not confirmation of Google indexing status.',
                        evidence={"url": page.url, "robots_meta": robots, "directive": directive},
                        observed_value=robots,
                        url=page.url,
                        page_id=page.id,
                        fingerprint=directive,
                    )
                )
    return findings


def _robots_txt_rules(pages: list[SeoCrawlPage]) -> list[FindingDraft]:
    findings: list[FindingDraft] = []
    robots_pages = [p for p in pages if (p.observations or {}).get("robots_txt")]
    blocked = [p for p in pages if p.error_code == "blocked_by_robots"]

    if not robots_pages:
        findings.append(
            FindingDraft(
                rule_id="SEO_ROBOTS_TXT_UNAVAILABLE",
                category="robots_txt",
                severity="INFO",
                title="robots.txt not observed",
                description="No successful robots.txt fetch was recorded during this crawl.",
                evidence={"root_scope": True},
                url=None,
                fingerprint="site",
            )
        )
    else:
        rp = robots_pages[0]
        if (rp.http_status or 0) >= 400:
            findings.append(
                FindingDraft(
                    rule_id="SEO_ROBOTS_TXT_ERROR",
                    category="robots_txt",
                    severity="LOW",
                    title="robots.txt returned error status",
                    description=f"robots.txt responded with HTTP {rp.http_status}.",
                    evidence={"url": rp.url, "http_status": rp.http_status},
                    url=rp.url,
                    page_id=rp.id,
                )
            )

    if blocked:
        findings.append(
            FindingDraft(
                rule_id="SEO_ROBOTS_TXT_BLOCKED_URLS",
                category="robots_txt",
                severity="INFO",
                title="URLs blocked by robots.txt",
                description=f"{len(blocked)} discovered URL(s) were not crawled because robots.txt disallows them.",
                evidence={
                    "blocked_count": len(blocked),
                    "sample_urls": [p.url for p in blocked[:10]],
                    "note": "Blocked by robots — not a failed HTTP request.",
                },
                url=None,
                fingerprint="blocked",
            )
        )
    return findings


def _sitemap_rules(pages: list[SeoCrawlPage], root: str, by_url: dict[str, SeoCrawlPage]) -> list[FindingDraft]:
    findings: list[FindingDraft] = []
    sitemap_pages = [p for p in pages if (p.observations or {}).get("sitemap")]
    if not sitemap_pages:
        findings.append(
            FindingDraft(
                rule_id="SEO_SITEMAP_NOT_OBSERVED",
                category="sitemap",
                severity="INFO",
                title="Sitemap not observed",
                description="No sitemap.xml was successfully fetched during this crawl.",
                evidence={"note": "Sitemap presence does not guarantee indexing."},
                url=None,
                fingerprint="site",
            )
        )
        return findings

    for sm in sitemap_pages:
        if sm.error_code or (sm.http_status or 0) >= 400:
            findings.append(
                FindingDraft(
                    rule_id="SEO_SITEMAP_INACCESSIBLE",
                    category="sitemap",
                    severity="LOW",
                    title="Sitemap inaccessible",
                    description=f"Sitemap URL returned HTTP {sm.http_status} or fetch error.",
                    evidence={"url": sm.url, "http_status": sm.http_status, "error_code": sm.error_code},
                    url=sm.url,
                    page_id=sm.id,
                )
            )
    return findings


def _link_rules(
    pages: list[SeoCrawlPage],
    by_url: dict[str, SeoCrawlPage],
    root: str,
    include_subdomains: bool,
) -> list[FindingDraft]:
    findings: list[FindingDraft] = []
    for page in pages:
        obs = page.observations or {}
        for link in obs.get("internal_links") or []:
            target = by_url.get(link)
            if not target:
                continue
            if target.error_code and target.error_code not in {"blocked_by_robots"}:
                findings.append(
                    FindingDraft(
                        rule_id="SEO_LINK_INTERNAL_BROKEN",
                        category="links",
                        severity="MEDIUM",
                        title="Broken internal link",
                        description=f"Internal link target returned error: {target.error_code or target.http_status}.",
                        evidence={"source_url": page.url, "target_url": link, "http_status": target.http_status, "error_code": target.error_code},
                        url=page.url,
                        page_id=page.id,
                        fingerprint=link,
                    )
                )
            elif (target.http_status or 0) >= 400:
                findings.append(
                    FindingDraft(
                        rule_id="SEO_LINK_INTERNAL_4XX_5XX",
                        category="links",
                        severity="MEDIUM",
                        title="Internal link to error page",
                        description=f"Internal link target returned HTTP {target.http_status}.",
                        evidence={"source_url": page.url, "target_url": link, "http_status": target.http_status},
                        url=page.url,
                        page_id=page.id,
                        fingerprint=link,
                    )
                )
        for link in obs.get("external_links") or []:
            if not normalize_url(link):
                findings.append(
                    FindingDraft(
                        rule_id="SEO_LINK_EXTERNAL_MALFORMED",
                        category="links",
                        severity="LOW",
                        title="Malformed external link",
                        description="External link href could not be normalized.",
                        evidence={"source_url": page.url, "href": link},
                        url=page.url,
                        page_id=page.id,
                        fingerprint=str(link)[:200],
                    )
                )
    return findings


def _redirect_rules(
    pages: list[SeoCrawlPage],
    root: str,
    include_subdomains: bool,
    t: AnalysisThresholds,
) -> list[FindingDraft]:
    findings: list[FindingDraft] = []
    for page in pages:
        if (page.redirect_count or 0) > 0:
            findings.append(
                FindingDraft(
                    rule_id="SEO_REDIRECT_OBSERVED",
                    category="redirects",
                    severity="INFO",
                    title="Redirecting page",
                    description=f"Page required {page.redirect_count} redirect hop(s) to reach final URL.",
                    evidence={
                        "url": page.url,
                        "final_url": page.final_url,
                        "redirect_count": page.redirect_count,
                    },
                    observed_value=str(page.redirect_count),
                    url=page.url,
                    page_id=page.id,
                )
            )
        if (page.redirect_count or 0) >= t.redirect_chain_warning:
            sev = "MEDIUM" if page.redirect_count >= t.redirect_chain_high else "LOW"
            findings.append(
                FindingDraft(
                    rule_id="SEO_REDIRECT_CHAIN",
                    category="redirects",
                    severity=sev,
                    title="Redirect chain observed",
                    description=f"Page passed through {page.redirect_count} redirects (heuristic warning threshold: {t.redirect_chain_warning}).",
                    evidence={"url": page.url, "redirect_count": page.redirect_count, "threshold": t.redirect_chain_warning},
                    url=page.url,
                    page_id=page.id,
                )
            )
        if page.final_url and page.url:
            final_norm = normalize_url(page.final_url)
            start_norm = normalize_url(page.url)
            if final_norm and start_norm and final_norm != start_norm:
                if not is_same_site(final_norm, root, include_subdomains=include_subdomains):
                    findings.append(
                        FindingDraft(
                            rule_id="SEO_REDIRECT_CROSS_HOST",
                            category="redirects",
                            severity="MEDIUM",
                            title="Redirect to different host",
                            description="Final URL after redirects is on a different host than the requested URL.",
                            evidence={"requested": page.url, "final_url": page.final_url},
                            url=page.url,
                            page_id=page.id,
                        )
                    )
        if page.final_url and page.url and normalize_url(page.final_url) == normalize_url(page.url) and (page.redirect_count or 0) >= 3:
            findings.append(
                FindingDraft(
                    rule_id="SEO_REDIRECT_LOOP_SUSPECT",
                    category="redirects",
                    severity="HIGH",
                    title="Suspected redirect loop",
                    description="Multiple redirects ended at the same URL — possible redirect loop.",
                    evidence={"url": page.url, "redirect_count": page.redirect_count},
                    url=page.url,
                    page_id=page.id,
                )
            )
    return findings


def _url_quality_rules(pages: list[SeoCrawlPage], root: str) -> list[FindingDraft]:
    findings: list[FindingDraft] = []
    norm_map: dict[str, list[str]] = defaultdict(list)
    slash_variants: dict[str, set[str]] = defaultdict(set)

    for page in pages:
        norm = normalize_url(page.url)
        if norm:
            norm_map[norm].append(page.url)
        path = urlparse(page.url).path or "/"
        base = path.rstrip("/") or "/"
        slash_variants[base].add(path)

    for norm, urls in norm_map.items():
        if len(urls) > 1:
            for url in urls:
                findings.append(
                    FindingDraft(
                        rule_id="SEO_URL_DUPLICATE_NORMALIZED",
                        category="url_quality",
                        severity="MEDIUM",
                        title="Duplicate normalized URL variant",
                        description="Multiple URL variants normalize to the same URL.",
                        evidence={"normalized": norm, "variants": urls},
                        observed_value=norm,
                        url=url,
                        fingerprint=norm,
                    )
                )

    for base, paths in slash_variants.items():
        if len(paths) > 1:
            findings.append(
                FindingDraft(
                    rule_id="SEO_URL_TRAILING_SLASH",
                    category="url_quality",
                    severity="INFO",
                    title="Inconsistent trailing slash",
                    description="Both trailing-slash and non-trailing-slash variants were discovered.",
                    evidence={"path_base": base, "variants": sorted(paths)},
                    url=None,
                    fingerprint=base,
                )
            )
    return findings


def _orphan_rules(pages: list[SeoCrawlPage], root: str, by_url: dict[str, SeoCrawlPage]) -> list[FindingDraft]:
    findings: list[FindingDraft] = []
    root_norm = normalize_url(root)
    inbound: dict[str, set[str]] = defaultdict(set)
    sitemap_urls: set[str] = set()

    for page in pages:
        if (page.observations or {}).get("sitemap"):
            continue
        obs = page.observations or {}
        for link in obs.get("internal_links") or []:
            inbound[link].add(page.url)
        if page.referrer_url:
            inbound[page.url].add(page.referrer_url)

    for page in pages:
        if (page.observations or {}).get("robots_txt") or (page.observations or {}).get("sitemap"):
            continue
        if page.error_code == "blocked_by_robots":
            continue
        page_norm = normalize_url(page.url)
        if page_norm == root_norm:
            continue
        if page.referrer_url:
            continue
        if not inbound.get(page.url) and page.depth == 0:
            continue
        if not inbound.get(page.url):
            source = "sitemap_or_seed" if page.depth == 0 else "crawl_graph"
            findings.append(
                FindingDraft(
                    rule_id="SEO_ORPHAN_PAGE",
                    category="orphan",
                    severity="INFO",
                    title="Page without internal inbound links",
                    description="No internal links from other crawled pages point to this URL.",
                    evidence={"url": page.url, "depth": page.depth, "discovery_source": source},
                    url=page.url,
                    page_id=page.id,
                    recommendation="Verify intentional discoverability via navigation or sitemap.",
                )
            )
    return findings


def _depth_rules(pages: list[SeoCrawlPage], t: AnalysisThresholds) -> list[FindingDraft]:
    findings: list[FindingDraft] = []
    for page in pages:
        if page.depth > t.recommended_max_depth:
            findings.append(
                FindingDraft(
                    rule_id="SEO_CRAWL_DEPTH_DEEP",
                    category="crawl_depth",
                    severity="INFO",
                    title="Deep crawl depth",
                    description=f"Page crawl depth ({page.depth}) exceeds configured heuristic ({t.recommended_max_depth}).",
                    evidence={"url": page.url, "depth": page.depth, "threshold": t.recommended_max_depth},
                    observed_value=str(page.depth),
                    expected_or_heuristic=f"<= {t.recommended_max_depth} (heuristic)",
                    url=page.url,
                    page_id=page.id,
                )
            )
    return findings


def _image_rules(pages: list[SeoCrawlPage], t: AnalysisThresholds) -> list[FindingDraft]:
    findings: list[FindingDraft] = []
    for page in pages:
        obs = page.observations or {}
        missing = int(obs.get("images_missing_alt") or 0)
        total = int(obs.get("images_total") or 0)
        if missing > 0:
            findings.append(
                FindingDraft(
                    rule_id="SEO_IMAGE_ALT_MISSING",
                    category="images",
                    severity="LOW",
                    title="Images missing alt attribute",
                    description=f"{missing} of {total} observed image(s) lack a non-empty alt attribute. Empty alt may be valid for decorative images — review context.",
                    evidence={"url": page.url, "images_total": total, "images_missing_alt": missing},
                    observed_value=str(missing),
                    url=page.url,
                    page_id=page.id,
                    recommendation="Add descriptive alt text where images convey meaning.",
                )
            )
    return findings


def _structured_data_rules(pages: list[SeoCrawlPage]) -> list[FindingDraft]:
    findings: list[FindingDraft] = []
    for page in pages:
        obs = page.observations or {}
        blocks = int(obs.get("structured_data_blocks") or 0)
        if blocks == 0:
            findings.append(
                FindingDraft(
                    rule_id="SEO_STRUCTURED_DATA_NONE",
                    category="structured_data",
                    severity="INFO",
                    title="No structured data observed",
                    description="No JSON-LD script blocks were detected. This does not prevent indexing.",
                    evidence={"url": page.url},
                    url=page.url,
                    page_id=page.id,
                )
            )
        elif blocks > 0:
            findings.append(
                FindingDraft(
                    rule_id="SEO_STRUCTURED_DATA_PRESENT",
                    category="structured_data",
                    severity="INFO",
                    title="Structured data observed",
                    description=f"Detected {blocks} JSON-LD block(s). Rich result eligibility is not guaranteed.",
                    evidence={"url": page.url, "blocks": blocks},
                    observed_value=str(blocks),
                    url=page.url,
                    page_id=page.id,
                )
            )
    return findings


def _hreflang_rules(pages: list[SeoCrawlPage], root: str, include_subdomains: bool) -> list[FindingDraft]:
    findings: list[FindingDraft] = []
    hreflang_index: dict[str, list[tuple[str, str]]] = defaultdict(list)

    for page in pages:
        obs = page.observations or {}
        tags = obs.get("hreflang") or []
        seen_codes: set[str] = set()
        for tag in tags:
            code = str(tag.get("hreflang") or "").strip()
            href = str(tag.get("href") or "").strip()
            if not code or not href:
                findings.append(
                    FindingDraft(
                        rule_id="SEO_HREFLANG_MALFORMED",
                        category="hreflang",
                        severity="LOW",
                        title="Malformed hreflang tag",
                        description="hreflang alternate link missing hreflang code or href.",
                        evidence={"url": page.url, "tag": tag},
                        url=page.url,
                        page_id=page.id,
                        fingerprint=json.dumps(tag, sort_keys=True),
                    )
                )
                continue
            if code in seen_codes:
                findings.append(
                    FindingDraft(
                        rule_id="SEO_HREFLANG_DUPLICATE",
                        category="hreflang",
                        severity="LOW",
                        title="Duplicate hreflang code",
                        description=f'Duplicate hreflang="{code}" on the same page.',
                        evidence={"url": page.url, "hreflang": code},
                        url=page.url,
                        page_id=page.id,
                        fingerprint=code,
                    )
                )
            seen_codes.add(code)
            if not re.match(r"^[a-z]{2}(-[a-z]{2})?$", code.lower()) and code.lower() != "x-default":
                findings.append(
                    FindingDraft(
                        rule_id="SEO_HREFLANG_INVALID_CODE",
                        category="hreflang",
                        severity="INFO",
                        title="Unrecognized hreflang code format",
                        description=f'hreflang="{code}" does not match common language/region patterns.',
                        evidence={"url": page.url, "hreflang": code},
                        url=page.url,
                        page_id=page.id,
                        fingerprint=code,
                    )
                )
            target = normalize_url(href, base_url=page.url)
            if target and not is_same_site(target, root, include_subdomains=include_subdomains):
                findings.append(
                    FindingDraft(
                        rule_id="SEO_HREFLANG_EXTERNAL_TARGET",
                        category="hreflang",
                        severity="INFO",
                        title="hreflang target outside site scope",
                        description="hreflang alternate points to a URL outside the crawled site scope.",
                        evidence={"url": page.url, "hreflang": code, "target": target},
                        url=page.url,
                        page_id=page.id,
                        fingerprint=code,
                    )
                )
            hreflang_index[target or href].append((page.url, code))

    return findings


def _protocol_rules(pages: list[SeoCrawlPage], root: str) -> list[FindingDraft]:
    findings: list[FindingDraft] = []
    root_scheme = urlparse(root).scheme.lower()
    for page in pages:
        scheme = urlparse(page.url).scheme.lower()
        if scheme == "http":
            findings.append(
                FindingDraft(
                    rule_id="SEO_HTTP_URL",
                    category="protocol",
                    severity="MEDIUM",
                    title="HTTP URL observed",
                    description="Page was crawled over HTTP. HTTPS is recommended for security and user trust.",
                    evidence={"url": page.url},
                    url=page.url,
                    page_id=page.id,
                )
            )
        obs = page.observations or {}
        for link in obs.get("internal_links") or []:
            if urlparse(link).scheme.lower() == "http" and root_scheme == "https":
                findings.append(
                    FindingDraft(
                        rule_id="SEO_MIXED_PROTOCOL_REFERENCE",
                        category="protocol",
                        severity="LOW",
                        title="HTTP internal reference on HTTPS site",
                        description="Page on HTTPS references an internal HTTP URL.",
                        evidence={"page_url": page.url, "http_link": link},
                        url=page.url,
                        page_id=page.id,
                        fingerprint=link,
                    )
                )
    return findings


def _status_content_rules(pages: list[SeoCrawlPage]) -> list[FindingDraft]:
    findings: list[FindingDraft] = []
    for page in pages:
        status = page.http_status
        if status and status >= 500:
            findings.append(
                FindingDraft(
                    rule_id="SEO_HTTP_5XX",
                    category="status",
                    severity="HIGH",
                    title="Server error response",
                    description=f"Page returned HTTP {status}.",
                    evidence={"url": page.url, "http_status": status},
                    observed_value=str(status),
                    url=page.url,
                    page_id=page.id,
                )
            )
        elif status and status >= 400 and page.error_code != "blocked_by_robots":
            findings.append(
                FindingDraft(
                    rule_id="SEO_HTTP_4XX",
                    category="status",
                    severity="MEDIUM",
                    title="Client error response",
                    description=f"Page returned HTTP {status}.",
                    evidence={"url": page.url, "http_status": status, "error_code": page.error_code},
                    observed_value=str(status),
                    url=page.url,
                    page_id=page.id,
                )
            )
        if _is_html_page(page) is False and not (page.observations or {}).get("robots_txt") and not (page.observations or {}).get("sitemap"):
            ct = page.content_type or "unknown"
            if "html" not in ct.lower() and status and status < 400:
                findings.append(
                    FindingDraft(
                        rule_id="SEO_UNEXPECTED_CONTENT_TYPE",
                        category="status",
                        severity="INFO",
                        title="Non-HTML content type",
                        description=f"URL returned content-type {ct} instead of HTML.",
                        evidence={"url": page.url, "content_type": ct},
                        observed_value=ct,
                        url=page.url,
                        page_id=page.id,
                    )
                )
        if (page.response_bytes or 0) == 0 and status and status < 400 and page.error_code != "blocked_by_robots":
            findings.append(
                FindingDraft(
                    rule_id="SEO_EMPTY_RESPONSE",
                    category="status",
                    severity="LOW",
                    title="Empty response body",
                    description="Successful response had zero bytes recorded.",
                    evidence={"url": page.url, "http_status": status},
                    url=page.url,
                    page_id=page.id,
                )
            )
    return findings
