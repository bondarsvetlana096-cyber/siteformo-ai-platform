from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Mapping

from app.services.voice_delivery.models import COUNTRY_CODES, E164

ACCOUNT_SID = re.compile(r"^AC[0-9a-fA-F]{32}$")


def flag(value: str | None) -> bool:
    normalized = (value or "false").strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"", "0", "false", "no", "off"}:
        return False
    raise ValueError("invalid_voice_demo_enabled")


@dataclass(frozen=True, slots=True)
class VoiceConfiguration:
    enabled: bool
    account_sid: str | None = None
    auth_token: str | None = field(default=None, repr=False)
    caller_e164: str | None = None
    allowed_countries: frozenset[str] = frozenset()
    public_base_url: str | None = None
    voice: str = "Polly.Amy-Neural"
    language: str = "en-GB"
    delay_seconds: int = 7
    recipient_limit: int = 1
    global_limit: int = 5

    def require_ready(self) -> None:
        if not self.enabled:
            raise ValueError("voice_demo_disabled")
        if not self.account_sid or not ACCOUNT_SID.fullmatch(self.account_sid):
            raise ValueError("voice_provider_not_configured")
        if not self.auth_token or not self.caller_e164 or not E164.fullmatch(self.caller_e164):
            raise ValueError("voice_provider_not_configured")
        if self.allowed_countries != frozenset({"IE"}):
            raise ValueError("voice_country_allowlist_not_ready")
        if not self.public_base_url or not self.public_base_url.startswith("https://"):
            raise ValueError("voice_callback_not_configured")
        if not 5 <= self.delay_seconds <= 10:
            raise ValueError("voice_delay_not_safe")
        if self.recipient_limit != 1 or not 1 <= self.global_limit <= 10:
            raise ValueError("voice_quota_not_safe")


def resolve_configuration(environment: Mapping[str, str]) -> VoiceConfiguration:
    raw_countries = environment.get("VOICE_DEMO_ALLOWED_COUNTRIES", "")
    countries = frozenset(x.strip().upper() for x in raw_countries.split(",") if x.strip())
    if countries - COUNTRY_CODES.keys():
        raise ValueError("unsupported_voice_country")
    try:
        delay = int(environment.get("VOICE_DEMO_DELAY_SECONDS", "7"))
        global_limit = int(environment.get("VOICE_DEMO_GLOBAL_LIMIT", "5"))
    except ValueError as exc:
        raise ValueError("invalid_voice_numeric_configuration") from exc
    return VoiceConfiguration(
        enabled=flag(environment.get("VOICE_DEMO_ENABLED")),
        account_sid=environment.get("TWILIO_VOICE_ACCOUNT_SID", "").strip() or None,
        auth_token=environment.get("TWILIO_VOICE_AUTH_TOKEN", "").strip() or None,
        caller_e164=environment.get("TWILIO_VOICE_FROM", "").strip() or None,
        allowed_countries=countries,
        public_base_url=environment.get("VOICE_DEMO_PUBLIC_BASE_URL", "").strip().rstrip("/") or None,
        voice=environment.get("TWILIO_VOICE_TTS_VOICE", "Polly.Amy-Neural").strip(),
        language=environment.get("TWILIO_VOICE_TTS_LANGUAGE", "en-GB").strip(),
        delay_seconds=delay,
        global_limit=global_limit,
    )
