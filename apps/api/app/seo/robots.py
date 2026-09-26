"""robots.txt fetch, parse, and allow/disallow evaluation."""

from __future__ import annotations

from dataclasses import dataclass, field
from urllib.parse import urljoin, urlparse


@dataclass
class RobotsRules:
    disallow: list[str] = field(default_factory=list)
    allow: list[str] = field(default_factory=list)
    crawl_delay: float | None = None
    sitemaps: list[str] = field(default_factory=list)


def parse_robots_txt(content: str) -> RobotsRules:
    rules = RobotsRules()
    current_agent: str | None = None
    for raw in content.splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line or ":" not in line:
            continue
        key, value = line.split(":", 1)
        key = key.strip().lower()
        value = value.strip()
        if key == "user-agent":
            current_agent = value.lower()
            continue
        if current_agent not in {"*", "growthos-seo-crawler"}:
            continue
        if key == "disallow" and value:
            rules.disallow.append(value)
        elif key == "allow" and value:
            rules.allow.append(value)
        elif key == "crawl-delay" and value:
            try:
                rules.crawl_delay = float(value)
            except ValueError:
                pass
        elif key == "sitemap" and value:
            rules.sitemaps.append(value)
    return rules


def path_allowed(rules: RobotsRules, path: str) -> bool:
    if not path.startswith("/"):
        path = "/" + path
    best_allow = -1
    best_disallow = -1
    for rule in rules.allow:
        if path.startswith(rule) and len(rule) > best_allow:
            best_allow = len(rule)
    for rule in rules.disallow:
        if path.startswith(rule) and len(rule) > best_disallow:
            best_disallow = len(rule)
    if best_allow >= best_disallow:
        return True
    return best_disallow < 0


def robots_url_for(root_url: str) -> str:
    parsed = urlparse(root_url)
    return f"{parsed.scheme}://{parsed.netloc}/robots.txt"
