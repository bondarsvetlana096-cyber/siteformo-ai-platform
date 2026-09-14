from __future__ import annotations

from collections.abc import Mapping
import os
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator, model_validator


class SitePlannerConfigV1(BaseModel):
    """Inert planner configuration. It reads no environment and holds no credentials."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    provider_identifier: str = Field(min_length=1, max_length=80)
    model_identifier: str = Field(min_length=1, max_length=120)
    provider_config_version: str = Field(min_length=1, max_length=80)
    planning_strategy_version: str = Field(default="single_call_v1", min_length=1, max_length=80)
    max_output_bytes: int = Field(default=131_072, ge=4_096, le=1_048_576)
    max_transport_retries: int = Field(default=1, ge=0, le=1)
    max_single_call_pages: int = Field(default=12, ge=1, le=100)
    projected_sections_per_page: int = Field(default=6, ge=1, le=20)
    max_projected_sections: int = Field(default=72, ge=1, le=500)
    max_stateful_requirements: int = Field(default=4, ge=0, le=20)


class OpenAISitePlannerConfigV1(BaseModel):
    """Dedicated, fail-closed OpenAI planner configuration."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    enabled: bool = False
    provider: Literal["openai"] | None = None
    api_key: SecretStr | None = None
    base_url: str = "https://api.openai.com/v1"
    model: Literal["gpt-5.6-sol", "gpt-6-astra"] | None = None
    reasoning_effort: Literal["medium", "high"] = "medium"
    service_tier: Literal["default"] = "default"
    timeout_seconds: int = Field(default=180, ge=30, le=600)
    max_provider_attempts: int = Field(default=2, ge=1, le=2)
    max_repair_attempts: Literal[1] = 1
    max_output_tokens: int = Field(default=24_576, ge=4_096, le=32_768)
    contract_version: Literal["v1"] = "v1"
    config_version: str = Field(default="openai-eval-v1", min_length=1, max_length=80)

    @field_validator("base_url")
    @classmethod
    def validate_base_url(cls, value: str) -> str:
        normalized = value.rstrip("/")
        if normalized not in {"https://api.openai.com/v1", "https://eu.api.openai.com/v1"}:
            raise ValueError("unsupported OpenAI API base URL")
        return normalized

    @model_validator(mode="after")
    def enabled_configuration_is_complete(self) -> "OpenAISitePlannerConfigV1":
        if self.enabled and self.provider is None:
            raise ValueError("enabled planner requires its dedicated provider")
        if self.enabled and (self.api_key is None or not self.api_key.get_secret_value().strip()):
            raise ValueError("enabled planner requires its dedicated API key")
        if self.enabled and self.model is None:
            raise ValueError("enabled planner requires an approved model")
        return self


_ENV_PREFIX = "SITEFORMO_SITE_PLANNER_"
_DECIMAL_INTEGER = re.compile(r"^[0-9]+$")


def _parse_environment_bool(name: str, raw: str) -> bool:
    value = raw.strip().lower()
    if value == "true":
        return True
    if value == "false":
        return False
    raise ValueError(f"{name} must be exactly true or false")


def _parse_environment_integer(name: str, raw: str) -> int:
    value = raw.strip()
    if not _DECIMAL_INTEGER.fullmatch(value):
        raise ValueError(f"{name} must be a base-10 unsigned integer")
    return int(value, 10)


def load_openai_site_planner_config_v1(
    environment: Mapping[str, str] | None = None,
) -> OpenAISitePlannerConfigV1:
    """Read only the dedicated planner namespace when explicitly invoked."""
    values = environment if environment is not None else os.environ
    names = {
        "enabled": "ENABLED", "provider": "PROVIDER", "api_key": "API_KEY",
        "base_url": "BASE_URL", "model": "MODEL", "reasoning_effort": "REASONING_EFFORT",
        "service_tier": "SERVICE_TIER", "timeout_seconds": "TIMEOUT_SECONDS",
        "max_provider_attempts": "MAX_PROVIDER_ATTEMPTS",
        "max_repair_attempts": "MAX_REPAIR_ATTEMPTS",
        "max_output_tokens": "MAX_OUTPUT_TOKENS", "contract_version": "CONTRACT_VERSION",
        "config_version": "CONFIG_VERSION",
    }
    payload: dict[str, object] = {}
    integer_fields = {
        "timeout_seconds", "max_provider_attempts", "max_repair_attempts",
        "max_output_tokens",
    }
    for field, suffix in names.items():
        name = _ENV_PREFIX + suffix
        if name not in values:
            continue
        raw = values[name]
        if not isinstance(raw, str):
            raise ValueError(f"{name} must be supplied as an environment string")
        if field == "enabled":
            payload[field] = _parse_environment_bool(name, raw)
        elif field in integer_fields:
            payload[field] = _parse_environment_integer(name, raw)
        else:
            payload[field] = raw.strip()
    return OpenAISitePlannerConfigV1.model_validate(payload)
