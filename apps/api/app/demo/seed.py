"""Seed demo organization data. Explicitly marked as demo — never presented as live integrations."""

from __future__ import annotations

import asyncio
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import func, select

from app.core.config import get_settings
from app.core.security import hash_password
from app.db.session import AsyncSessionLocal, engine
from app.db.base import Base
import app.models  # noqa: F401
from app.models.ai_ops import AIRecommendation, Integration, Subscription
from app.models.automation import AutonomySettings, OptimizationRule
from app.models.client import Client
from app.models.enums import AutonomyMode, LeadStatus, MemberRole, Priority, RecommendationStatus
from app.models.leads import Lead
from app.models.marketing import AnalyticsDaily, Campaign, Competitor, SocialPost
from app.models.organization import Organization, OrganizationMember
from app.models.user import User
from app.services.autonomy_service import DEFAULT_ALLOWED_ACTIONS, DEFAULT_PLATFORMS


async def _ensure_phase2_enrichment(db, org: Organization, clients: list[Client]) -> None:
    """Idempotent Phase 2 demo enrichment for existing installs."""
    # Extend analytics coverage to ~90 days where missing
    for client in clients:
        existing_dates = {
            row
            for row in (
                await db.execute(
                    select(AnalyticsDaily.date).where(
                        AnalyticsDaily.organization_id == org.id,
                        AnalyticsDaily.client_id == client.id,
                    )
                )
            ).scalars().all()
        }
        for i in range(90):
            d = date.today() - timedelta(days=89 - i)
            if d in existing_dates:
                continue
            # Older period slightly more efficient so comparisons show movement
            spend = Decimal("90") + Decimal(i * 2) + (Decimal("30") if "Lumen" in client.business_name else 0)
            leads = 3 + (i % 4) if i < 45 else 2 + (i % 5)
            revenue = Decimal(leads * 180)
            db.add(
                AnalyticsDaily(
                    organization_id=org.id,
                    client_id=client.id,
                    date=d,
                    spend=spend,
                    leads=leads,
                    revenue=revenue,
                    impressions=3500 + i * 100,
                    clicks=160 + i * 3,
                    conversions=1 + (i % 3),
                    metrics={"source": "demo", "phase": 2},
                    data_source="demo",
                )
            )

        post_count = await db.scalar(
            select(func.count()).select_from(SocialPost).where(
                SocialPost.organization_id == org.id, SocialPost.client_id == client.id
            )
        )
        if not post_count:
            for idx, (platform, ctype, hook, impressions, engagement, ctr) in enumerate(
                [
                    ("Instagram", "Reel", "Stop guessing your creative", 12000, 980, 2.1),
                    ("LinkedIn", "Post", "Ops teams waste 6 hrs/week", 5400, 410, 1.4),
                    ("Instagram", "Carousel", "3 offer angles that convert", 8200, 620, 1.8),
                    ("WhatsApp", "Broadcast", "This week’s consult slots", None, None, None),
                ]
            ):
                metrics = {}
                if impressions is not None:
                    metrics = {"impressions": impressions, "engagement": engagement, "ctr": ctr}
                db.add(
                    SocialPost(
                        organization_id=org.id,
                        client_id=client.id,
                        platform=platform,
                        content_type=ctype,
                        hook=hook,
                        main_copy=f"Demo {ctype} for {client.business_name}",
                        cta="Book a call",
                        visual_concept="Clean brand frame",
                        video_concept="Hook → proof → CTA" if ctype == "Reel" else None,
                        hashtags=["#GrowthOS", "#DemoData"],
                        status="published",
                        metrics=metrics,
                        data_source="demo",
                    )
                )

        comp_count = await db.scalar(
            select(func.count()).select_from(Competitor).where(
                Competitor.organization_id == org.id, Competitor.client_id == client.id
            )
        )
        if not comp_count:
            for name in (client.competitors or ["Competitor A", "Competitor B"])[:2]:
                db.add(
                    Competitor(
                        organization_id=org.id,
                        client_id=client.id,
                        name=name,
                        url=f"https://example.com/{name.lower().replace(' ', '-')}",
                        notes="Seeded competitor observation (demo).",
                        observations={
                            "positioning": "Similar offer category",
                            "channels": client.primary_channels[:2] if client.primary_channels else ["Insufficient data."],
                            "notes": "No invented performance claims.",
                        },
                    )
                )

        camp_count = await db.scalar(
            select(func.count()).select_from(Campaign).where(
                Campaign.organization_id == org.id, Campaign.client_id == client.id
            )
        )
        if camp_count and camp_count < 2:
            db.add(
                Campaign(
                    organization_id=org.id,
                    client_id=client.id,
                    name=f"{client.business_name} Retargeting",
                    platform="meta",
                    status="active",
                    objective="conversions",
                    spend=Decimal("1100"),
                    metrics={"ctr": 2.4, "cpl": 31.0, "leads": 35, "note": "Demo campaign metrics"},
                    data_source="demo",
                )
            )


class SeedBlockedError(RuntimeError):
    """Raised when demo seeding is attempted in an environment that forbids it."""


def assert_seeding_allowed() -> None:
    """
    Demo seeding writes fake organizations, clients, leads and analytics.
    It must never run against a production database.
    """
    settings = get_settings()
    if settings.is_production:
        raise SeedBlockedError(
            "DEMO SEEDING BLOCKED: ENVIRONMENT=production. "
            "Demo data must never be written to a production database. "
            "Run the seeder only with ENVIRONMENT=development or staging."
        )
    if not settings.allow_demo_seed:
        raise SeedBlockedError(
            "DEMO SEEDING BLOCKED: ALLOW_DEMO_SEED=false. "
            "Set ALLOW_DEMO_SEED=true in a development or staging environment to seed demo data."
        )


async def seed() -> None:
    assert_seeding_allowed()
    settings = get_settings()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with AsyncSessionLocal() as db:
        existing = await db.scalar(select(User).where(User.email == "demo@growthos.ai"))
        if existing:
            org = await db.scalar(select(Organization).where(Organization.slug == "northstar-agency"))
            if org:
                clients = list(
                    (await db.execute(select(Client).where(Client.organization_id == org.id))).scalars().all()
                )
                await _ensure_phase2_enrichment(db, org, clients)
                await _ensure_phase5_enrichment(db, org)
                await db.commit()
            print("Demo data already seeded (demo@growthos.ai). Phase 2/5 enrichment applied if needed.")
            return

        user = User(
            email="demo@growthos.ai",
            hashed_password=hash_password("demo1234"),
            full_name="Alex Rivera",
            last_login_at=datetime.now(timezone.utc),
        )
        org = Organization(name="Northstar Agency", slug="northstar-agency", demo_mode=True, plan="pro")
        db.add(user)
        db.add(org)
        await db.flush()
        db.add(OrganizationMember(organization_id=org.id, user_id=user.id, role=MemberRole.owner))
        db.add(Subscription(organization_id=org.id, plan="pro", status="active", seats=5))

        clients_data = [
            {
                "business_name": "ASC Dental",
                "industry": "Healthcare",
                "website": "https://ascdental.example",
                "description": "Premium dental clinic focused on cosmetic and family care.",
                "location": "Austin, TX",
                "target_audience": "Adults 25-55 seeking cosmetic and preventive dental care",
                "products_services": "Implants, Invisalign, Whitening, Family Dentistry",
                "marketing_goals": "Increase qualified consult bookings and reduce CPL",
                "monthly_budget": Decimal("8500"),
                "brand_voice": "Warm, clinical-trust, clear, reassuring",
                "competitors": ["SmileCo", "Bright Dental Hub"],
                "primary_channels": ["Meta Ads", "Instagram", "Google Ads"],
                "kpis": ["CPL", "Booked consults", "ROAS"],
            },
            {
                "business_name": "Lumen SaaS",
                "industry": "B2B Software",
                "website": "https://lumensaas.example",
                "description": "Analytics platform for mid-market ops teams.",
                "location": "Remote / US",
                "target_audience": "Ops leaders at 50-500 employee companies",
                "products_services": "Ops analytics, dashboards, automation alerts",
                "marketing_goals": "Grow demo pipeline and improve SQL rate",
                "monthly_budget": Decimal("15000"),
                "brand_voice": "Sharp, credible, operator-friendly",
                "competitors": ["Databox", "Geckoboard"],
                "primary_channels": ["LinkedIn", "Google Ads", "Content"],
                "kpis": ["SQL", "Demo rate", "CAC"],
            },
            {
                "business_name": "Harbor Fitness",
                "industry": "Fitness",
                "website": "https://harborfitness.example",
                "description": "Boutique strength and conditioning studio.",
                "location": "Seattle, WA",
                "target_audience": "Professionals 28-45 wanting accountable training",
                "products_services": "Memberships, PT, Group classes",
                "marketing_goals": "Fill class capacity and improve trial-to-member conversion",
                "monthly_budget": Decimal("4000"),
                "brand_voice": "Energetic, grounded, community-first",
                "competitors": ["Iron Room", "Forge Athletics"],
                "primary_channels": ["Instagram", "WhatsApp", "Meta Ads"],
                "kpis": ["Trial signups", "CPL", "Retention"],
            },
        ]

        clients: list[Client] = []
        for item in clients_data:
            client = Client(organization_id=org.id, **item)
            db.add(client)
            clients.append(client)
        await db.flush()

        # Demo analytics (explicit demo source)
        for client in clients:
            for i in range(30):
                d = date.today() - timedelta(days=29 - i)
                spend = Decimal("120") + Decimal(i * 3) + (Decimal("40") if client.business_name == "Lumen SaaS" else 0)
                leads = 2 + (i % 5)
                revenue = Decimal(leads * 180)
                db.add(
                    AnalyticsDaily(
                        organization_id=org.id,
                        client_id=client.id,
                        date=d,
                        spend=spend,
                        leads=leads,
                        revenue=revenue,
                        impressions=4000 + i * 120,
                        clicks=180 + i * 4,
                        conversions=1 + (i % 3),
                        metrics={"source": "demo"},
                        data_source="demo",
                    )
                )
            db.add(
                Campaign(
                    organization_id=org.id,
                    client_id=client.id,
                    name=f"{client.business_name} Prospecting",
                    platform="meta",
                    status="active",
                    objective="leads",
                    spend=Decimal("2400"),
                    metrics={"ctr": 1.8, "cpl": 42.5, "note": "Demo campaign metrics"},
                    data_source="demo",
                )
            )

        # Leads
        lead_seed = [
            ("Jordan Lee", "jordan@example.com", "Meta Ads", "ASC Prospecting", LeadStatus.qualified),
            ("Sam Patel", "sam@example.com", "Website Form", "Retargeting", LeadStatus.new),
            ("Casey Nguyen", "casey@example.com", "LinkedIn", "Thought Leadership", LeadStatus.meeting),
            ("Riley Chen", "riley@example.com", "Instagram", "Reels Push", LeadStatus.contacted),
            ("Avery Brooks", "avery@example.com", "Referral", None, LeadStatus.interested),
        ]
        for idx, (name, email, source, campaign, lead_status) in enumerate(lead_seed):
            client = clients[idx % len(clients)]
            score = 55 + idx * 7
            db.add(
                Lead(
                    organization_id=org.id,
                    client_id=client.id,
                    name=name,
                    email=email,
                    phone=f"+1-555-010{idx}",
                    source=source,
                    campaign=campaign,
                    ad="Demo Ad A" if campaign else None,
                    lead_score=score,
                    score_explanation={
                        "score": score,
                        "reasons": [
                            "Email present",
                            f"Source attributed: {source}",
                            "Score is based only on available CRM fields. Behavioral events unavailable.",
                        ],
                        "based_on_available_data_only": True,
                        "insufficient_data_note": "Score is based only on available information.",
                    },
                    status=lead_status,
                    notes="Seeded demo lead",
                    last_activity_at=datetime.now(timezone.utc) - timedelta(hours=idx * 5),
                )
            )

        # AI recommendations for dashboard
        db.add(
            AIRecommendation(
                organization_id=org.id,
                client_id=clients[0].id,
                title="Meta CPL increased 27% for ASC",
                problem="Prospecting efficiency declined week over week.",
                evidence="Demo analytics show ASC CPL rising from prior period baseline.",
                recommendation="Review Campaign B and create 3 new creative variations.",
                priority=Priority.high,
                expected_impact="Stabilize CPL within 2 weeks",
                status=RecommendationStatus.pending,
            )
        )
        db.add(
            AIRecommendation(
                organization_id=org.id,
                client_id=clients[1].id,
                title="LinkedIn content cadence gap",
                problem="Inconsistent posting reduces pipeline warmth.",
                evidence="Content calendar density below target for Lumen SaaS (demo).",
                recommendation="Ship 3 authority posts and 1 case-study carousel this week.",
                priority=Priority.medium,
                expected_impact="Improve demo request rate",
                status=RecommendationStatus.pending,
            )
        )

        phase_by_provider = {
            "meta": 3,
            "instagram": 3,
            "whatsapp": 3,
            "google_analytics": 3,
            "google_ads": 4,
            "youtube": 4,
        }
        for provider, phase in phase_by_provider.items():
            db.add(
                Integration(
                    organization_id=org.id,
                    provider=provider,
                    status="demo_data" if settings.demo_mode else "not_connected",
                    config={
                        "phase": phase,
                        "note": "Seeded placeholder — not a live connection until OAuth completes",
                    },
                )
            )

        await db.flush()
        await _ensure_phase2_enrichment(db, org, clients)
        await _ensure_phase5_enrichment(db, org)
        await db.commit()
        print("Seeded demo user: demo@growthos.ai / demo1234")
        print(f"Organization: {org.name} (demo_mode=True)")


async def _ensure_phase5_enrichment(db, org: Organization) -> None:
    existing = await db.scalar(
        select(AutonomySettings).where(
            AutonomySettings.organization_id == org.id, AutonomySettings.client_id.is_(None)
        )
    )
    if not existing:
        db.add(
            AutonomySettings(
                organization_id=org.id,
                client_id=None,
                autonomy_mode=AutonomyMode.copilot,
                automation_enabled=False,
                allowed_platforms=list(DEFAULT_PLATFORMS),
                allowed_actions=list(DEFAULT_ALLOWED_ACTIONS),
            )
        )
    rule_count = await db.scalar(
        select(func.count()).select_from(OptimizationRule).where(OptimizationRule.organization_id == org.id)
    )
    if not rule_count:
        db.add(
            OptimizationRule(
                organization_id=org.id,
                name="CPL spike → creative refresh",
                enabled=True,
                condition={
                    "target_cpl": 35,
                    "cpl_multiplier": 1.3,
                    "minimum_spend": 100,
                    "minimum_conversions": 3,
                },
                action_template={
                    "recommendation": "Create three new creative variations and review weakest campaign spend."
                },
                priority=Priority.high,
            )
        )
    await db.flush()


if __name__ == "__main__":
    import sys

    try:
        asyncio.run(seed())
    except SeedBlockedError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc
