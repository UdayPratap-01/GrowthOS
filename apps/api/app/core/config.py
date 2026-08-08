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
    demo_mode: bool = True
    api_cors_origins: str = "http://localhost:3000,http://127.0.0.1:3000"

    database_url: str = "sqlite+aiosqlite:///./growthos.db"
    database_url_sync: str = "sqlite:///./growthos.db"

    secret_key: str = "dev-secret-change-me"
    access_token_expire_minutes: int = 60
    refresh_token_expire_days: int = 14
    algorithm: str = "HS256"

    encryption_key: str = "change-me-32-byte-fernet-compatible-key!!"
    ai_provider: str = "mock"  # mock | openai | anthropic
    openai_api_key: str = ""
    anthropic_api_key: str = ""

    storage_backend: str = "local"
    storage_local_path: str = "./storage"
    rate_limit_per_minute: int = 120

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

    # Phase 5 — generation providers: none | demo
    image_provider: str = "none"
    video_provider: str = "none"
    meta_webhook_verify_token: str = ""

    @property
    def cors_origins(self) -> list[str]:
        return [o.strip() for o in self.api_cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
