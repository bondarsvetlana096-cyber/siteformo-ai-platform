from pathlib import Path

from pydantic import AliasChoices, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


BASE_DIR = Path(__file__).resolve().parents[3]
DEFAULT_SQLITE_PATH = BASE_DIR / "siteformo_local.db"
DEFAULT_DATABASE_URL = f"sqlite:///{DEFAULT_SQLITE_PATH.as_posix()}"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(".env", ".env.local"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # App/runtime
    app_name: str = "SiteFormo AI Sales Platform"
    app_env: str = Field(default="production", validation_alias=AliasChoices("APP_ENV", "ENV"))
    public_base_url: str = "http://127.0.0.1:8000"
    database_url: str | None = None
    log_level: str = Field(default="info", validation_alias=AliasChoices("LOG_LEVEL", "LOGLEVEL"))

    # Security/admin
    admin_api_key: str | None = None
    approval_secret: str = "change-me-super-secret-string"
    asset_signing_secret: str = "change-me-asset-signing-secret"
    turnstile_secret_key: str | None = None
    allow_manual_decision_without_token: bool = True
    rate_limit_enabled: bool = True
    rate_limit_per_hour: int = 60
    free_attempt_limit: int = 1
    bypass_limit_emails: str | None = None
    payment_approval_bypass_emails: str | None = "klon97048@gmail.com"

    # Owner/contact notifications
    owner_email: str | None = "klon97048@gmail.com"
    owner_telegram_chat_id: str | None = None
    enable_owner_notifications: bool = True
    divi_export_email: str | None = None

    # OpenAI/AI
    openai_api_key: str | None = None
    openai_model: str = "gpt-5.1-mini"
    openai_timeout_seconds: float = 25.0
    openai_max_retries: int = 2
    ai_memory_max_history: int = 12
    ai_memory_ttl_seconds: int = 86400
    ai_state_ttl_seconds: int = 86400
    max_user_message_chars: int = 3000
    enable_lead_extraction: bool = True
    enable_db_leads: bool = True

    # Telegram
    telegram_bot_token: str | None = None
    telegram_bot_username: str | None = None
    telegram_contact_label: str | None = None

    # WhatsApp / Messenger
    whatsapp_provider: str | None = "twilio"
    whatsapp_webhook_verify_token: str | None = None
    whatsapp_public_number: str | None = None
    whatsapp_contact_number: str | None = None
    whatsapp_contact_label: str | None = None
    whatsapp_api_key: str | None = None
    whatsapp_phone_number_id: str | None = None
    whatsapp_twilio_account_sid: str | None = None
    whatsapp_twilio_auth_token: str | None = None
    whatsapp_twilio_number: str | None = None
    messenger_contact_url: str | None = None
    messenger_contact_label: str | None = None

    # Email
    smtp_host: str | None = None
    smtp_port: int | None = 587
    smtp_user: str | None = None
    smtp_password: str | None = None
    smtp_from: str | None = None

    # Demo/publishing/follow-ups
    main_site_base_url: str | None = "https://siteformo.com"
    main_site_continue_path: str | None = "/continue"
    main_site_checkout_path: str | None = "/checkout"
    demo_ttl_minutes: int = 10
    demo_retention_hours: int = 96
    demo_storage_dir: str | None = "./demo_storage"
    demo_protection_enabled: bool = True
    demo_ready_followup_delay_minutes: int = 30
    demo_cta_followup_delay_minutes: int = 180
    checkout_followup_delay_minutes: int = 1440
    max_followup_count: int = 3

    # Guided sales flow / lead nurturing
    enable_guided_followups: bool = True
    guided_followup_poll_seconds: int = 60
    guided_followup_stage_1_minutes: int = 5
    guided_followup_stage_2_minutes: int = 60
    guided_followup_stage_3_minutes: int = 1440
    guided_followup_stage_4_minutes: int = 4320
    guided_followup_max_stage: int = 4
    guided_followup_send_to_lead: bool = False
    offer_output_dir: str | None = "app/static/offers"

    # Queue/storage/Supabase/S3
    queue_backend: str | None = "inline"
    queue_poll_seconds: int = 5
    queue_visibility_timeout_seconds: int = 60
    storage_backend: str | None = "auto"

    redis_url: str | None = None
    redis_host: str | None = None
    redis_port: int | None = 6379
    redis_password: str | None = None

    supabase_url: str | None = None
    supabase_service_role_key: str | None = None
    supabase_storage_bucket: str | None = None

    s3_endpoint_url: str | None = None
    s3_access_key_id: str | None = None
    s3_secret_access_key: str | None = None
    s3_region: str | None = None
    s3_bucket: str | None = None

    postgres_url: str | None = None
    postgresql_url: str | None = None
    supabase_db_url: str | None = None

    # Telemetry
    sentry_dsn: str | None = None
    posthog_api_key: str | None = None
    posthog_host: str | None = None

    @field_validator(
        "app_name",
        "app_env",
        "database_url",
        "public_base_url",
        "approval_secret",
        "asset_signing_secret",
        "owner_email",
        "payment_approval_bypass_emails",
        "bypass_limit_emails",
        "openai_api_key",
        "openai_model",
        "telegram_bot_token",
        "telegram_bot_username",
        "telegram_contact_label",
        "whatsapp_provider",
        "whatsapp_webhook_verify_token",
        "whatsapp_public_number",
        "whatsapp_contact_number",
        "whatsapp_contact_label",
        "whatsapp_api_key",
        "whatsapp_phone_number_id",
        "whatsapp_twilio_account_sid",
        "whatsapp_twilio_auth_token",
        "whatsapp_twilio_number",
        "messenger_contact_url",
        "messenger_contact_label",
        "smtp_host",
        "smtp_user",
        "smtp_password",
        "smtp_from",
        "divi_export_email",
        "main_site_base_url",
        "main_site_continue_path",
        "main_site_checkout_path",
        "demo_storage_dir",
        "offer_output_dir",
        "queue_backend",
        "storage_backend",
        "redis_url",
        "redis_host",
        "redis_password",
        "supabase_url",
        "supabase_service_role_key",
        "supabase_storage_bucket",
        "s3_endpoint_url",
        "s3_access_key_id",
        "s3_secret_access_key",
        "s3_region",
        "s3_bucket",
        "postgres_url",
        "postgresql_url",
        "supabase_db_url",
        "sentry_dsn",
        "posthog_api_key",
        "posthog_host",
        "turnstile_secret_key",
        "admin_api_key",
        "owner_telegram_chat_id",
        mode="before",
    )
    @classmethod
    def normalize_blank_strings(cls, value: object):
        if value is None:
            return None
        if isinstance(value, str):
            cleaned = value.strip()
            if not cleaned:
                return None
            return cleaned
        return value

    @field_validator("database_url", mode="after")
    @classmethod
    def default_database_url(cls, value: str | None) -> str:
        return value or DEFAULT_DATABASE_URL

    @field_validator("bypass_limit_emails", "payment_approval_bypass_emails", mode="after")
    @classmethod
    def default_empty_string(cls, value: str | None) -> str:
        return value or ""

    @field_validator("posthog_host", mode="after")
    @classmethod
    def default_posthog_host(cls, value: str | None) -> str:
        return value or "https://app.posthog.com"

    @field_validator("whatsapp_provider", "queue_backend", "storage_backend", mode="after")
    @classmethod
    def normalize_lowercase(cls, value: str | None) -> str:
        return (value or "").strip().lower()

    @property
    def ENV(self) -> str:
        return self.app_env

    @property
    def LOG_LEVEL(self) -> str:
        return self.log_level

    @property
    def ADMIN_API_KEY(self) -> str | None:
        return self.admin_api_key

    @property
    def OWNER_TELEGRAM_CHAT_ID(self) -> str | None:
        return self.owner_telegram_chat_id

    @property
    def ENABLE_OWNER_NOTIFICATIONS(self) -> bool:
        return self.enable_owner_notifications

    @property
    def OPENAI_API_KEY(self) -> str | None:
        return self.openai_api_key

    @property
    def OPENAI_MODEL(self) -> str:
        return self.openai_model

    @property
    def OPENAI_TIMEOUT_SECONDS(self) -> float:
        return self.openai_timeout_seconds

    @property
    def OPENAI_MAX_RETRIES(self) -> int:
        return self.openai_max_retries

    @property
    def AI_MEMORY_MAX_HISTORY(self) -> int:
        return self.ai_memory_max_history

    @property
    def AI_MEMORY_TTL_SECONDS(self) -> int:
        return self.ai_memory_ttl_seconds

    @property
    def AI_STATE_TTL_SECONDS(self) -> int:
        return self.ai_state_ttl_seconds

    @property
    def RATE_LIMIT_MESSAGES(self) -> int:
        return self.rate_limit_per_hour

    @property
    def RATE_LIMIT_WINDOW_SECONDS(self) -> int:
        return 3600

    @property
    def MAX_USER_MESSAGE_CHARS(self) -> int:
        return self.max_user_message_chars

    @property
    def ENABLE_LEAD_EXTRACTION(self) -> bool:
        return self.enable_lead_extraction

    @property
    def ENABLE_DB_LEADS(self) -> bool:
        return self.enable_db_leads


    
    def ENABLE_GUIDED_FOLLOWUPS(self) -> bool:
        return self.enable_guided_followups

    
    def GUIDED_FOLLOWUP_SEND_TO_LEAD(self) -> bool:
        return self.guided_followup_send_to_lead

settings = Settings()