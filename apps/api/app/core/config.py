from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(".env", "../../.env", "../../../.env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "GrowthOS AI"
    api_v1_prefix: str = "/api/v1"
    # development | staging | production
    environment: str = "development"
    # Demo mode simulates executions. It must be opt-in, never a silent fallback.
    demo_mode: bool = False
    # Second safety latch for the demo seeder. Refused outright in production.
    allow_demo_seed: bool = True
    api_cors_origins: str = "http://localhost:3000,http://127.0.0.1:3000"

    database_url: str = "sqlite+aiosqlite:///./growthos.db"
    database_url_sync: str = "sqlite:///./growthos.db"
    # Create tables at startup via metadata.create_all.
    # Unset -> enabled in development only. Production must use Alembic migrations.
    db_auto_create: bool | None = None

    secret_key: str = "dev-secret-change-me"
    access_token_expire_minutes: int = 60
    refresh_token_expire_days: int = 14
    algorithm: str = "HS256"

    # The refresh token is delivered as an httpOnly cookie so browser code never
    # touches it. Secure must be on in production; it is off by default only so
    # that plain-HTTP local development works.
    refresh_cookie_secure: bool | None = None
    refresh_cookie_samesite: str = "lax"
    refresh_cookie_domain: str = ""

    encryption_key: str = "change-me-32-byte-fernet-compatible-key!!"
    ai_provider: str = "mock"  # mock | openai | anthropic
    openai_api_key: str = ""
    anthropic_api_key: str = ""

    # ---- Object storage ------------------------------------------------
    # local  -> development only; container disks are ephemeral.
    # s3     -> any S3-compatible provider (AWS S3, Cloudflare R2, MinIO, ...).
    storage_backend: str = "local"
    storage_local_path: str = "./storage"
    s3_bucket: str = ""
    s3_region: str = "auto"
    # Leave empty for AWS S3; set for R2/MinIO/Wasabi/etc.
    s3_endpoint_url: str = ""
    s3_access_key_id: str = ""
    s3_secret_access_key: str = ""
    # Required by MinIO and some proxies; harmless on AWS.
    s3_force_path_style: bool = False
    # Presigned GET lifetime. Short by default: these URLs bypass app authorization.
    s3_signed_url_expiry_seconds: int = 900

    # ---- Observability ---------------------------------------------------
    # json | text. Unset -> text in development, json elsewhere.
    log_format: str = ""
    log_level: str = "INFO"
    # Include exception detail in API error responses. Never enable in production.
    debug_errors: bool = False

    # ---- Background jobs -------------------------------------------------
    # Run enqueued work inside the HTTP request instead of a worker process.
    # Unset -> enabled in development only. Refused in production: a 3-minute
    # video generation must never block a request.
    inline_job_execution: bool | None = None
    worker_poll_interval_seconds: float = 2.0
    worker_batch_size: int = 5
    worker_lease_seconds: int = 300
    # How long a video job may sit at the provider before it is failed.
    video_job_timeout_seconds: int = 1800

    # ---- Rate limiting -------------------------------------------------
    # Shared backend across API instances. Required in production.
    redis_url: str = ""
    # General authenticated traffic.
    rate_limit_per_minute: int = 120
    # Unauthenticated credential endpoints, per IP and per account identifier.
    auth_rate_limit_per_minute: int = 10
    auth_rate_limit_per_ip_per_minute: int = 30
    # Endpoints that cost money on every call.
    ai_rate_limit_per_minute: int = 20
    media_rate_limit_per_hour: int = 60
    report_rate_limit_per_hour: int = 30
    campaign_execution_rate_limit_per_minute: int = 10
    webhook_rate_limit_per_minute: int = 300
    # When the shared backend is unreachable, degrade to per-process limits
    # rather than failing every request. Set false to fail closed instead.
    rate_limit_degrade_to_local: bool = True

    # ---- Client IP / proxy trust ----------------------------------------
    # Who is allowed to tell us the client's address via X-Forwarded-For.
    #   ""      -> unset. The header is ignored; the socket peer is the client.
    #   "none"  -> explicit statement that the app is exposed directly.
    #   "1.2.3.4,10.0.0.0/8" -> trust the header only from these peers.
    #   "*"     -> trust any peer. Development only; refused in production.
    # Anything else, including an unset value, means an attacker cannot reset
    # their own rate-limit budget by inventing a header.
    trusted_proxy_ips: str = ""

    # ---- Monitoring ----------------------------------------------------
    # Bearer token the scraper presents to /metrics. Required in production:
    # the endpoint reveals traffic shape, error rates and provider names, and
    # it sits outside the authenticated API.
    metrics_token: str = ""

    # Public URLs
    api_public_url: str = "http://127.0.0.1:8000"
    frontend_url: str = "http://127.0.0.1:3000"

    # Phase 3 — Meta family (Meta Ads / Instagram / WhatsApp)
    meta_app_id: str = ""
    meta_app_secret: str = ""
    meta_redirect_uri: str = ""

    # Phase 3 — Google Analytics (shared Google OAuth client for Ads/YouTube)
    google_client_id: str = ""
    google_client_secret: str = ""
    google_redirect_uri: str = ""

    # Phase 4 — Google Ads
    google_ads_developer_token: str = ""
    google_ads_login_customer_id: str = ""  # optional MCC
    google_ads_redirect_uri: str = ""

    # Phase 4 — YouTube
    youtube_redirect_uri: str = ""

    # Media generation — none | demo | openai (images); none | demo | replicate (videos)
    image_provider: str = "none"
    image_api_key: str = ""
    image_model: str = "gpt-image-1"
    video_provider: str = "none"
    video_api_key: str = ""
    video_model: str = ""  # replicate owner/name or version hash
    meta_webhook_verify_token: str = ""

    # ---- P2-A campaign generation guardrails ----------------------------
    # Hard ceilings applied server-side to one "Generate Campaign" request.
    # Every image and video below costs real provider money, so these are the
    # control that stops a scripted or mistyped request from generating
    # hundreds of assets. Frontend inputs are a convenience, never the limit.
    max_concepts_per_generation: int = 5
    max_images_per_generation: int = 8
    max_videos_per_generation: int = 4
    max_variations_per_generation: int = 12
    # Ceiling on generation runs an organization may start per hour, on top of
    # the per-metric quota. Guards against a retry loop in a client.
    campaign_generation_rate_limit_per_hour: int = 20

    # ---- Scheduled autopilot ------------------------------------------------
    # Disabled by default. When enabled, the worker enqueues bounded autopilot
    # cycle jobs via the existing PostgreSQL-backed job queue.
    autopilot_scheduler_enabled: bool = False
    autopilot_interval_minutes: int = 60
    autopilot_max_orgs_per_cycle: int = 10

    # ---- Stale AI action execution recovery --------------------------------
    autonomous_execution_stale_timeout_minutes: int = 30
    autonomous_execution_stale_recovery_batch_size: int = 50

    # ---- Analytics ingestion ------------------------------------------------
    # Pulls Meta/Google Ads performance into marketing_performance_daily via
    # JobQueue. Disabled only as an emergency latch — default on for connected
    # integrations when operators enqueue ingest jobs.
    analytics_ingestion_enabled: bool = True
    analytics_ingestion_lookback_days: int = 7
    analytics_ingestion_max_lookback_days: int = 30
    analytics_ingestion_batch_size: int = 500

    # ---- Performance intelligence (analysis-only) ---------------------------
    # Thresholds for deterministic signal detection. Recommendations never
    # execute Meta/Google mutations in this milestone.
    performance_min_spend: float = 50.0
    performance_min_impressions: int = 1000
    performance_min_clicks: int = 20
    performance_min_conversions: float = 1.0
    performance_significant_change_percent: float = 20.0
    performance_sudden_change_percent: float = 50.0
    performance_min_days_with_data: int = 3
    performance_recommendation_ttl_days: int = 14

    # ---- Closed-loop optimization (Milestone 3) ------------------------------
    # Disabled by default so existing orgs do not become autonomous accidentally.
    # Decisions create AIActions only through ActionService / ExecutionEngine.
    optimization_enabled: bool = False
    optimization_min_confidence: float = 0.55
    optimization_cooldown_hours: int = 24
    optimization_opposite_cooldown_hours: int = 48
    optimization_max_actions_per_day: int = 10
    optimization_max_consecutive_budget_increases: int = 2
    optimization_min_campaign_budget: float = 5.0
    # Autonomous mode may only auto-create/execute up to this risk (HIGH never).
    optimization_max_autonomous_risk: str = "low"
    # Evidence older than this cannot drive mutations (hours).
    optimization_max_evidence_age_hours: int = 72

    # ---- Production safety / canary (Milestone 4) — ALL default OFF ----------
    # Layered gates: global AND provider AND optimization AND org AND canary.
    autonomous_execution_enabled: bool = False
    meta_autonomous_enabled: bool = False
    google_autonomous_enabled: bool = False
    # Emergency stop: blocks NEW autonomous mutations; does not delete recommendations.
    autonomous_kill_switch: bool = False
    # Canary allowlists — empty means none (safe). Comma-separated.
    autonomous_canary_org_ids: str = ""
    autonomous_canary_providers: str = ""  # meta,google
    autonomous_canary_action_types: str = ""  # update_budget,pause_campaign,...
    # Absolute daily spend-impact ceiling for autonomous budget mutations (currency units).
    # 0 = no additional absolute cap beyond org AutonomySettings.
    autonomous_max_daily_spend_impact: float = 0.0
    autonomous_max_campaigns_per_cycle: int = 1
    # Live provider verification harness (never runs in normal pytest).
    provider_verification_enabled: bool = False
    provider_verification_org_id: str = ""
    provider_verification_client_id: str = ""
    provider_verification_meta_campaign_id: str = ""
    provider_verification_google_campaign_id: str = ""
    provider_verification_confirm: str = ""  # must equal "I_CONFIRM_LIVE_MUTATIONS"
    # Phase 1/2: verification snapshot older than this blocks live canary.
    provider_verification_max_age_hours: int = 24

    # ---- Live operator canary (M5 Phase 2) — ALL default OFF / empty ----------
    # Empty allowlists mean NO canary execution (never "allow all").
    canary_enabled: bool = False
    canary_allowed_org_ids: str = ""
    canary_allowed_providers: str = ""  # meta,google_ads
    canary_allowed_meta_ad_accounts: str = ""
    canary_allowed_meta_campaigns: str = ""
    canary_allowed_google_customers: str = ""
    canary_allowed_google_campaigns: str = ""
    canary_allowed_actions: str = ""  # pause_campaign,resume_campaign only recommended
    canary_allowed_environments: str = ""  # empty = none; e.g. development,staging
    canary_max_actions_per_run: int = 1
    canary_max_actions_per_day: int = 1
    # Pause/resume have zero spend impact; budget canaries still check this when enabled.
    canary_max_spend_impact: float = 0.0

    @property
    def cors_origins(self) -> list[str]:
        return [o.strip() for o in self.api_cors_origins.split(",") if o.strip()]

    @property
    def env(self) -> str:
        return (self.environment or "development").strip().lower()

    @property
    def is_production(self) -> bool:
        return self.env == "production"

    @property
    def is_development(self) -> bool:
        return self.env == "development"

    @property
    def refresh_cookie_is_secure(self) -> bool:
        """Never send the refresh cookie over plain HTTP outside development."""
        if self.refresh_cookie_secure is None:
            return not self.is_development
        return self.refresh_cookie_secure

    @property
    def should_run_jobs_inline(self) -> bool:
        """
        Inline execution keeps a single-process dev setup usable. Production
        always uses the worker, so a long generation cannot occupy a request.
        """
        if self.is_production:
            return False
        if self.inline_job_execution is None:
            return self.is_development
        return self.inline_job_execution

    @property
    def should_auto_create_tables(self) -> bool:
        """create_all is a development convenience. Production always uses Alembic."""
        if self.is_production:
            return False
        if self.db_auto_create is None:
            return self.is_development
        return self.db_auto_create


@lru_cache
def get_settings() -> Settings:
    return Settings()
