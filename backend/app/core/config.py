from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_name: str = "Siteformo API"
    app_env: str = "development"

    api_base_url: str = Field(default="http://localhost:8000", alias="APP_BASE_URL")
    frontend_base_url: str = Field(default="https://demo.siteformo.com", alias="FRONTEND_BASE_URL")
    demo_base_url: str = Field(default="https://demo-siteformo.com", alias="DEMO_BASE_URL")
    main_site_base_url: str = Field(default="https://siteformo.com", alias="MAIN_SITE_BASE_URL")
    main_site_continue_path: str = Field(default="/continue", alias="MAIN_SITE_CONTINUE_PATH")
    main_site_checkout_path: str = Field(default="/checkout", alias="MAIN_SITE_CHECKOUT_PATH")
    pricing_redirect_url: str = Field(default="https://siteformo.com/pricing", alias="PRICING_REDIRECT_URL")

    database_url: str = Field(alias="DATABASE_URL")

    openai_api_key: str = ""
    openai_model: str = "gpt-4.1-mini"
    resend_api_key: str = ""
    resend_from_email: str = "demo@siteformo.com"
    turnstile_secret_key: str = ""
    posthog_api_key: str = ""
    posthog_host: str = "https://eu.i.posthog.com"
    sentry_dsn: str = ""

    supabase_url: str = ""
    supabase_service_role_key: str = ""
    supabase_storage_bucket: str = "siteformo-demo"
    supabase_project_ref: str = ""
    supabase_db_schema: str = "public"

    storage_backend: str = "local"
    queue_backend: str = "supabase"
    demo_storage_dir: str = "./generated_demos"
    s3_bucket: str = ""
    s3_region: str = "us-east-1"
    s3_endpoint_url: str = ""
    s3_access_key_id: str = ""
    s3_secret_access_key: str = ""

    telegram_bot_username: str = ""
    telegram_contact_label: str = ""
    whatsapp_contact_number: str = ""
    messenger_contact_url: str = ""
    messenger_contact_label: str = ""

    free_attempt_limit: int = 999
    demo_ttl_minutes: int = 10
    demo_retention_hours: int = 96
    rate_limit_enabled: bool = True
    rate_limit_per_hour: int = 20
    queue_poll_seconds: int = 2
    queue_visibility_timeout_seconds: int = 60
    asset_signing_secret: str = "change-me"
    demo_protection_enabled: bool = True
    demo_bind_ip_enabled: bool = True
    demo_session_cookie_enabled: bool = True
    demo_session_cookie_name: str = "sf_demo_session"

    demo_ready_followup_delay_minutes: int = 120
    demo_cta_followup_delay_minutes: int = 1440
    checkout_followup_delay_minutes: int = 180
    max_followup_count: int = 3

    playwright_headless: bool = True
    playwright_timeout_ms: int = 25000


settings = Settings()
