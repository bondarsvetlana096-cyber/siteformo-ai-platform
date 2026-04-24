import os


class Settings:
    ADMIN_API_KEY = os.getenv("ADMIN_API_KEY")
    OWNER_TELEGRAM_CHAT_ID = os.getenv("OWNER_TELEGRAM_CHAT_ID")
    ENABLE_OWNER_NOTIFICATIONS = os.getenv("ENABLE_OWNER_NOTIFICATIONS", "true").lower() == "true"

    ENV = os.getenv("ENV", "production")
    LOG_LEVEL = os.getenv("LOG_LEVEL", "info")

    OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
    OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
    OPENAI_TIMEOUT_SECONDS = float(os.getenv("OPENAI_TIMEOUT_SECONDS", "25"))
    OPENAI_MAX_RETRIES = int(os.getenv("OPENAI_MAX_RETRIES", "2"))

    AI_MEMORY_MAX_HISTORY = int(os.getenv("AI_MEMORY_MAX_HISTORY", "12"))
    AI_MEMORY_TTL_SECONDS = int(os.getenv("AI_MEMORY_TTL_SECONDS", "86400"))
    AI_STATE_TTL_SECONDS = int(os.getenv("AI_STATE_TTL_SECONDS", "86400"))

    RATE_LIMIT_MESSAGES = int(os.getenv("RATE_LIMIT_MESSAGES", "20"))
    RATE_LIMIT_WINDOW_SECONDS = int(os.getenv("RATE_LIMIT_WINDOW_SECONDS", "60"))

    MAX_USER_MESSAGE_CHARS = int(os.getenv("MAX_USER_MESSAGE_CHARS", "3000"))

    ENABLE_LEAD_EXTRACTION = os.getenv("ENABLE_LEAD_EXTRACTION", "true").lower() == "true"
    ENABLE_DB_LEADS = os.getenv("ENABLE_DB_LEADS", "true").lower() == "true"


settings = Settings()
