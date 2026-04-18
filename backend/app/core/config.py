from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_env: str = "development"

    public_base_url: str = "http://localhost:8000"
    app_base_url: str | None = None
    frontend_base_url: str | None = None
    demo_base_url: str | None = None
    pricing_redirect_url: str = "https://siteformo.com/pricing"
    database_url: str

    queue_backend: str = "pgmq"
    queue_visibility_timeout_seconds: int = 60
    queue_poll_seconds: int = 2
    queue_poll_interval_seconds: int = 2
    queue_batch_size: int = 1

    storage_backend: str = "local"
    demo_storage_dir: str = "./storage"

    supabase_url: str | None = None
    supabase_service_role_key: str | None = None
    supabase_storage_bucket: str = "demo-storage"

    s3_endpoint_url: str | None = None
    s3_access_key_id: str | None = None
    s3_secret_access_key: str | None = None
    s3_region: str = "us-east-1"
    s3_bucket: str | None = None

    demo_ttl_hours: int = 24
    demo_ttl_minutes: int = 24 * 60
    demo_retention_days: int = 30
    demo_retention_hours: int = 30 * 24

    demo_bind_ip_enabled: bool = False
    demo_session_cookie_enabled: bool = True
    demo_session_cookie_name: str = "siteformo_demo_session"

    asset_signing_secret: str = Field(..., min_length=32)
    secret_key: str | None = Field(default=None, min_length=32)

    free_attempt_limit: int = 3
    demo_ready_followup_delay_minutes: int = 10
    demo_cta_followup_delay_minutes: int = 60
    checkout_followup_delay_minutes: int = 120
    max_followup_count: int = 3

    turnstile_secret_key: str | None = None

    smtp_host: str | None = None
    smtp_port: int = 587
    smtp_username: str | None = None
    smtp_password: str | None = None
    smtp_from_email: str | None = None
    smtp_from_name: str = "Siteformo"

    resend_api_key: str | None = None
    resend_from_email: str | None = None

    openai_api_key: str | None = None

    railway_static_url: str | None = None
    info: str | None = None
    start_command: str | None = None


settings = Settings()