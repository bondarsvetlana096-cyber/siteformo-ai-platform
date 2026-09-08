from __future__ import annotations

from functools import lru_cache

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class AssistantSettings(BaseSettings):
    """Assistant-only settings; legacy OPENAI_* names are never aliases."""

    model_config = SettingsConfigDict(
        env_file=(".env", ".env.local"),
        env_file_encoding="utf-8",
        env_prefix="SITEFORMO_ASSISTANT_",
        extra="ignore",
        case_sensitive=False,
    )

    enabled: bool = False
    openai_api_key: str | None = None
    openai_model: str = "gpt-5.6-luna"
    openai_timeout_seconds: float = Field(default=30.0, gt=0, le=300)
    openai_max_retries: int = Field(default=2, ge=0, le=10)

    @field_validator("enabled", mode="before")
    @classmethod
    def parse_enabled_strictly(cls, value: object) -> bool:
        if isinstance(value, bool):
            return value
        if value is None:
            return False
        normalized = str(value).strip().lower()
        if normalized == "true":
            return True
        if normalized == "false":
            return False
        raise ValueError("SITEFORMO_ASSISTANT_ENABLED must be exactly true or false")

    @field_validator("openai_api_key", "openai_model", mode="before")
    @classmethod
    def strip_optional_text(cls, value: object) -> str | None:
        if value is None:
            return None
        text = str(value).strip()
        return text or None

    def require_inference_configuration(self) -> None:
        if not self.enabled:
            raise AssistantDisabledError("Assistant is disabled")
        if not self.openai_api_key:
            raise AssistantConfigurationError("Assistant inference is not configured")
        if not self.openai_model:
            raise AssistantConfigurationError("Assistant model is not configured")


class AssistantDisabledError(RuntimeError):
    pass


class AssistantConfigurationError(RuntimeError):
    pass


@lru_cache(maxsize=1)
def get_assistant_settings() -> AssistantSettings:
    return AssistantSettings()
