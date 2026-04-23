from pathlib import Path

from pydantic import field_validator
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

    app_name: str = "SiteFormo AI Sales Platform"
    database_url: str = DEFAULT_DATABASE_URL
    public_base_url: str = "http://127.0.0.1:8000"
    approval_secret: str = "change-me-super-secret-string"
    owner_email: str = "klon97048@gmail.com"
    allow_manual_decision_without_token: bool = True
    payment_approval_bypass_emails: str = "klon97048@gmail.com"

    openai_api_key: str | None = None
    openai_model: str = "gpt-5.1-mini"

    telegram_bot_token: str | None = None
    telegram_bot_username: str | None = None

    whatsapp_provider: str = "twilio"
    whatsapp_webhook_verify_token: str | None = None
    whatsapp_public_number: str | None = None

    # Meta / WhatsApp Cloud API
    whatsapp_api_key: str | None = None
    whatsapp_phone_number_id: str | None = None

    # Twilio WhatsApp Sandbox / sender
    whatsapp_twilio_account_sid: str | None = None
    whatsapp_twilio_auth_token: str | None = None
    whatsapp_twilio_number: str | None = None

    smtp_host: str | None = None
    smtp_port: int | None = 587
    smtp_user: str | None = None
    smtp_password: str | None = None
    smtp_from: str | None = None
    divi_export_email: str | None = None

    @field_validator(
        "database_url",
        "public_base_url",
        "approval_secret",
        "owner_email",
        "payment_approval_bypass_emails",
        "openai_api_key",
        "telegram_bot_token",
        "telegram_bot_username",
        "whatsapp_provider",
        "whatsapp_webhook_verify_token",
        "whatsapp_public_number",
        "whatsapp_api_key",
        "whatsapp_phone_number_id",
        "whatsapp_twilio_account_sid",
        "whatsapp_twilio_auth_token",
        "whatsapp_twilio_number",
        "smtp_host",
        "smtp_user",
        "smtp_password",
        "smtp_from",
        "divi_export_email",
        mode="before",
    )
    @classmethod
    def normalize_blank_strings(cls, value: object):
        if value is None:
            return value
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

    @field_validator("whatsapp_provider", mode="after")
    @classmethod
    def normalize_whatsapp_provider(cls, value: str | None) -> str:
        return (value or "twilio").strip().lower()


settings = Settings()
