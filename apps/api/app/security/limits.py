"""
Organization-scoped rate limits for operations that cost money.

Keyed on the organization rather than the user: a member cannot get extra budget
by rotating user accounts, and one tenant cannot consume another's allowance.

Attach as an extra route dependency so it composes with `require_permission`:

    @router.post("/images/generate", dependencies=[Depends(media_limit)])
"""

from __future__ import annotations

from fastapi import Depends

from app.core.deps import AuthContext, get_current_auth
from app.security.rate_limit import enforce, policies


def org_limited(policy_name: str):
    async def _dependency(auth: AuthContext = Depends(get_current_auth)) -> None:
        await enforce(f"org:{auth.organization_id}", policies()[policy_name], scope=policy_name)

    return _dependency


ai_limit = org_limited("ai")
media_limit = org_limited("media")
report_limit = org_limited("report")
campaign_execution_limit = org_limited("campaign_execution")
campaign_generation_limit = org_limited("campaign_generation")
seo_crawl_limit = org_limited("seo_crawl")
gsc_sync_limit = org_limited("gsc_sync")
keyword_analyze_limit = org_limited("keyword_analyze")
topic_analyze_limit = org_limited("topic_analyze")
seo_competitor_crawl_limit = org_limited("seo_competitor_crawl")
content_gap_analyze_limit = org_limited("content_gap_analyze")
seo_recommendation_generate_limit = org_limited("seo_recommendation_generate")
seo_content_brief_generate_limit = org_limited("seo_content_brief_generate")
seo_content_generate_limit = org_limited("seo_content_generate")
seo_onpage_optimize_limit = org_limited("seo_onpage_optimize")
