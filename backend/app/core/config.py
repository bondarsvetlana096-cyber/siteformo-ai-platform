from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


BASE_DIR = Path(__file__).resolve().parents[3]
DEFAULT_SQLITE_PATH = BASE_DIR / 'siteformo_local.db'
DEFAULT_DATABASE_URL = f"sqlite:///{DEFAULT_SQLITE_PATH.as_posix()}"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file='.env', env_file_encoding='utf-8', extra='ignore')

    app_name: str = 'SiteFormo AI Sales Platform'
    database_url: str = DEFAULT_DATABASE_URL
    public_base_url: str = 'http://127.0.0.1:8000'
    approval_secret: str = 'change-me-super-secret-string'
    owner_email: str = 'klon97048@gmail.com'
    allow_manual_decision_without_token: bool = True
    payment_approval_bypass_emails: str = 'klon97048@gmail.com'
    telegram_bot_username: str | None = None
    whatsapp_public_number: str | None = None

    openai_api_key: str | None = None
    openai_model: str = 'gpt-5.1-mini'
    whatsapp_provider: str | None = None
    whatsapp_api_key: str | None = None
    whatsapp_phone_number_id: str | None = None
    whatsapp_webhook_verify_token: str | None = None
    telegram_bot_token: str | None = None

    smtp_host: str | None = None
    smtp_port: int | None = 587
    smtp_user: str | None = None
    smtp_password: str | None = None
    smtp_from: str | None = None
    divi_export_email: str | None = None


settings = Settings()
