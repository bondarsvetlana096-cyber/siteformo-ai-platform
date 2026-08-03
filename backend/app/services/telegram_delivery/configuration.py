from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Mapping

from app.services.telegram_delivery.models import USERNAME


class Readiness(StrEnum):
    READY = "READY"
    DISABLED = "DISABLED"
    INCOMPLETE = "INCOMPLETE"
    INVALID = "INVALID"


@dataclass(frozen=True, slots=True)
class TelegramConfiguration:
    bot_token: str = field(repr=False)
    bot_username: str
    webhook_secret: str = field(repr=False)
    binding_namespace: str
    binding_ttl_seconds: int
    channel_enabled: bool


@dataclass(frozen=True, slots=True)
class ConfigurationResult:
    readiness: Readiness
    configuration: TelegramConfiguration | None = None
    missing: tuple[str, ...] = ()


def webhook_ready(result: ConfigurationResult, *, redis_available: bool) -> bool:
    return bool(
        result.readiness is Readiness.READY
        and result.configuration
        and result.configuration.webhook_secret
        and redis_available
    )


def resolve_configuration(environment: Mapping[str, str]) -> ConfigurationResult:
    required = {
        "TELEGRAM_BOT_TOKEN": environment.get("TELEGRAM_BOT_TOKEN", "").strip(),
        "TELEGRAM_BOT_USERNAME": environment.get("TELEGRAM_BOT_USERNAME", "").strip().lstrip("@"),
        "TELEGRAM_WEBHOOK_SECRET_TOKEN": environment.get("TELEGRAM_WEBHOOK_SECRET_TOKEN", "").strip(),
        "TELEGRAM_VISITOR_BINDING_NAMESPACE": environment.get("TELEGRAM_VISITOR_BINDING_NAMESPACE", "").strip(),
        "TELEGRAM_VISITOR_BINDING_TTL_SECONDS": environment.get("TELEGRAM_VISITOR_BINDING_TTL_SECONDS", "").strip(),
    }
    missing = tuple(name for name, value in required.items() if not value)
    if missing:
        return ConfigurationResult(Readiness.INCOMPLETE, missing=missing)
    enabled_raw = environment.get("ENABLE_TELEGRAM_CHANNEL", "").strip().lower()
    if enabled_raw not in {"1", "true", "yes", "on", "0", "false", "no", "off", ""}:
        return ConfigurationResult(Readiness.INVALID)
    enabled = enabled_raw in {"1", "true", "yes", "on"}
    try:
        ttl = int(required["TELEGRAM_VISITOR_BINDING_TTL_SECONDS"])
    except ValueError:
        return ConfigurationResult(Readiness.INVALID)
    if not USERNAME.fullmatch(required["TELEGRAM_BOT_USERNAME"]):
        return ConfigurationResult(Readiness.INVALID)
    if ttl < 60 or ttl > 3600 or not required["TELEGRAM_VISITOR_BINDING_NAMESPACE"].startswith("sf:demo-telegram:v1:"):
        return ConfigurationResult(Readiness.INVALID)
    configuration = TelegramConfiguration(
        bot_token=required["TELEGRAM_BOT_TOKEN"],
        bot_username=required["TELEGRAM_BOT_USERNAME"],
        webhook_secret=required["TELEGRAM_WEBHOOK_SECRET_TOKEN"],
        binding_namespace=required["TELEGRAM_VISITOR_BINDING_NAMESPACE"],
        binding_ttl_seconds=ttl,
        channel_enabled=enabled,
    )
    return ConfigurationResult(Readiness.READY if enabled else Readiness.DISABLED, configuration)
