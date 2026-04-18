from __future__ import annotations

from functools import lru_cache
from typing import Optional

from pydantic import AliasChoices, Field, computed_field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file='.env',
        env_file_encoding='utf-8',
        extra='ignore',
        case_sensitive=False,
    )

    # App
    app_name: str = Field(default='Siteformo API', validation_alias=AliasChoices('APP_NAME'))
    app_env: str = Field(default='local', validation_alias=AliasChoices('APP_ENV'))
    app_host: str = Field(default='0.0.0.0', validation_alias=AliasChoices('APP_HOST'))
    app_port: int = Field(default=8000, validation_alias=AliasChoices('APP_PORT'))
    app_debug: bool = Field(default=False, validation_alias=AliasChoices('APP_DEBUG'))
    log_level: str = Field(default='INFO', validation_alias=AliasChoices('LOG_LEVEL'))
    app_base_url: Optional[str] = Field(default=None, validation_alias=AliasChoices('APP_BASE_URL'))
    frontend_base_url: Optional[str] = Field(default=None, validation_alias=AliasChoices('FRONTEND_BASE_URL'))
    public_base_url: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices('PUBLIC_BASE_URL', 'DEMO_BASE_URL', 'APP_BASE_URL'),
    )

    # Main site links
    main_site_base_url: str = Field(default='https://siteformo.com', validation_alias=AliasChoices('MAIN_SITE_BASE_URL'))
    main_site_continue_path: str = Field(default='/continue', validation_alias=AliasChoices('MAIN_SITE_CONTINUE_PATH'))
    main_site_checkout_path: str = Field(default='/checkout', validation_alias=AliasChoices('MAIN_SITE_CHECKOUT_PATH'))
    pricing_redirect_url: str = Field(default='https://siteformo.com/pricing', validation_alias=AliasChoices('PRICING_REDIRECT_URL'))

    # Database
    database_url: str = Field(..., validation_alias=AliasChoices('DATABASE_URL'))

    # OpenAI
    openai_api_key: Optional[str] = Field(default=None, validation_alias=AliasChoices('OPENAI_API_KEY'))
    openai_model: str = Field(default='gpt-4.1-mini', validation_alias=AliasChoices('OPENAI_MODEL'))

    # Email / captcha / telemetry
    resend_api_key: Optional[str] = Field(default=None, validation_alias=AliasChoices('RESEND_API_KEY'))
    resend_from_email: str = Field(default='SiteFormo <hello@siteformo.com>', validation_alias=AliasChoices('RESEND_FROM_EMAIL'))
    turnstile_secret_key: Optional[str] = Field(default=None, validation_alias=AliasChoices('TURNSTILE_SECRET_KEY'))
    posthog_api_key: Optional[str] = Field(default=None, validation_alias=AliasChoices('POSTHOG_API_KEY'))
    posthog_host: str = Field(default='https://eu.i.posthog.com', validation_alias=AliasChoices('POSTHOG_HOST'))
    sentry_dsn: Optional[str] = Field(default=None, validation_alias=AliasChoices('SENTRY_DSN'))

    # Supabase / storage
    supabase_url: Optional[str] = Field(default=None, validation_alias=AliasChoices('SUPABASE_URL'))
    supabase_anon_key: Optional[str] = Field(default=None, validation_alias=AliasChoices('SUPABASE_ANON_KEY'))
    supabase_service_role_key: Optional[str] = Field(default=None, validation_alias=AliasChoices('SUPABASE_SERVICE_ROLE_KEY'))
    supabase_storage_bucket: str = Field(default='siteformo-demo', validation_alias=AliasChoices('SUPABASE_STORAGE_BUCKET'))
    supabase_project_ref: Optional[str] = Field(default=None, validation_alias=AliasChoices('SUPABASE_PROJECT_REF'))
    supabase_db_schema: str = Field(default='public', validation_alias=AliasChoices('SUPABASE_DB_SCHEMA'))

    storage_backend: str = Field(default='local', validation_alias=AliasChoices('STORAGE_BACKEND'))
    demo_storage_dir: str = Field(default='./generated_demos', validation_alias=AliasChoices('DEMO_STORAGE_DIR'))

    s3_bucket: Optional[str] = Field(default=None, validation_alias=AliasChoices('S3_BUCKET'))
    s3_region: str = Field(default='us-east-1', validation_alias=AliasChoices('S3_REGION'))
    s3_endpoint_url: Optional[str] = Field(default=None, validation_alias=AliasChoices('S3_ENDPOINT_URL'))
    s3_access_key_id: Optional[str] = Field(default=None, validation_alias=AliasChoices('S3_ACCESS_KEY_ID'))
    s3_secret_access_key: Optional[str] = Field(default=None, validation_alias=AliasChoices('S3_SECRET_ACCESS_KEY'))

    # Queue
    queue_backend: str = Field(default='db', validation_alias=AliasChoices('QUEUE_BACKEND'))
    queue_poll_seconds: float = Field(default=2.0, validation_alias=AliasChoices('QUEUE_POLL_SECONDS', 'QUEUE_POLL_INTERVAL_SEC'))
    queue_visibility_timeout_seconds: int = Field(default=60, validation_alias=AliasChoices('QUEUE_VISIBILITY_TIMEOUT_SECONDS'))

    # UX / rate limits / protection
    free_attempt_limit: int = Field(default=2, validation_alias=AliasChoices('FREE_ATTEMPT_LIMIT'))
    demo_ttl_minutes: int = Field(default=10, validation_alias=AliasChoices('DEMO_TTL_MINUTES'))
    demo_retention_hours: int = Field(default=96, validation_alias=AliasChoices('DEMO_RETENTION_HOURS'))
    rate_limit_enabled: bool = Field(default=False, validation_alias=AliasChoices('RATE_LIMIT_ENABLED'))
    rate_limit_per_hour: int = Field(default=20, validation_alias=AliasChoices('RATE_LIMIT_PER_HOUR'))
    asset_signing_secret: str = Field(default='change-me', validation_alias=AliasChoices('ASSET_SIGNING_SECRET', 'SECRET_KEY'))
    demo_protection_enabled: bool = Field(default=True, validation_alias=AliasChoices('DEMO_PROTECTION_ENABLED'))
    demo_bind_ip_enabled: bool = Field(default=True, validation_alias=AliasChoices('DEMO_BIND_IP_ENABLED'))
    demo_session_cookie_enabled: bool = Field(default=True, validation_alias=AliasChoices('DEMO_SESSION_COOKIE_ENABLED'))
    demo_session_cookie_name: str = Field(default='sf_demo_session', validation_alias=AliasChoices('DEMO_SESSION_COOKIE_NAME'))

    # Follow-ups
    demo_ready_followup_delay_minutes: int = Field(default=120, validation_alias=AliasChoices('DEMO_READY_FOLLOWUP_DELAY_MINUTES'))
    demo_cta_followup_delay_minutes: int = Field(default=1440, validation_alias=AliasChoices('DEMO_CTA_FOLLOWUP_DELAY_MINUTES'))
    checkout_followup_delay_minutes: int = Field(default=180, validation_alias=AliasChoices('CHECKOUT_FOLLOWUP_DELAY_MINUTES'))
    max_followup_count: int = Field(default=3, validation_alias=AliasChoices('MAX_FOLLOWUP_COUNT'))

    # Contact channels
    telegram_bot_username: Optional[str] = Field(default=None, validation_alias=AliasChoices('TELEGRAM_BOT_USERNAME'))
    telegram_contact_label: Optional[str] = Field(default='@siteformo_bot', validation_alias=AliasChoices('TELEGRAM_CONTACT_LABEL'))
    whatsapp_contact_number: Optional[str] = Field(default=None, validation_alias=AliasChoices('WHATSAPP_CONTACT_NUMBER'))
    messenger_contact_url: Optional[str] = Field(default=None, validation_alias=AliasChoices('MESSENGER_CONTACT_URL'))
    messenger_contact_label: Optional[str] = Field(default='Siteformo Messenger', validation_alias=AliasChoices('MESSENGER_CONTACT_LABEL'))

    # Browser automation
    playwright_headless: bool = Field(default=True, validation_alias=AliasChoices('PLAYWRIGHT_HEADLESS'))
    playwright_timeout_ms: int = Field(default=25000, validation_alias=AliasChoices('PLAYWRIGHT_TIMEOUT_MS'))

    @field_validator('app_env', mode='before')
    @classmethod
    def _normalize_app_env(cls, value: object) -> str:
        if value is None:
            return 'local'
        text = str(value).strip().lower()
        aliases = {
            'development': 'dev',
            'production': 'prod',
            'stage': 'staging',
        }
        return aliases.get(text, text)

    @field_validator('queue_backend', 'storage_backend', mode='before')
    @classmethod
    def _normalize_backend(cls, value: object) -> str:
        if value is None:
            return 'db'
        return str(value).strip().lower()

    @computed_field
    @property
    def is_local(self) -> bool:
        return self.app_env == 'local'

    @computed_field
    @property
    def is_dev(self) -> bool:
        return self.app_env in {'local', 'dev'}

    @computed_field
    @property
    def is_prod(self) -> bool:
        return self.app_env == 'prod'

    @computed_field
    @property
    def demo_ttl_hours(self) -> float:
        return self.demo_ttl_minutes / 60.0

    @computed_field
    @property
    def demo_retention_days(self) -> float:
        return self.demo_retention_hours / 24.0

    def masked_dict(self) -> dict:
        data = self.model_dump()
        for key in (
            'database_url',
            'openai_api_key',
            'resend_api_key',
            'turnstile_secret_key',
            'posthog_api_key',
            'sentry_dsn',
            'supabase_anon_key',
            'supabase_service_role_key',
            'asset_signing_secret',
            's3_access_key_id',
            's3_secret_access_key',
        ):
            if data.get(key):
                data[key] = '***'
        return data


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
