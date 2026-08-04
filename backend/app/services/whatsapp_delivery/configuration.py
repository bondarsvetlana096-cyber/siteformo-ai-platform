from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Mapping

from app.services.whatsapp_delivery.models import normalize_e164

ACCOUNT_SID = re.compile(r"^AC[0-9a-fA-F]{32}$")
CONTENT_SID = re.compile(r"^HX[0-9a-fA-F]{32}$")
MESSAGING_SERVICE_SID = re.compile(r"^MG[0-9a-fA-F]{32}$")


class Readiness(StrEnum):
    READY = "RAILWAY_TWILIO_CONFIGURATION_READY"
    PARTIAL = "RAILWAY_TWILIO_CONFIGURATION_PARTIAL"
    PROVIDER_MISMATCH = "RAILWAY_TWILIO_PROVIDER_MISMATCH"
    UNAVAILABLE = "RAILWAY_TWILIO_CONFIGURATION_UNAVAILABLE"


class FlagStatus(StrEnum):
    ENABLED = "ENABLED"
    DISABLED = "DISABLED"
    INVALID = "INVALID"
    ABSENT = "ABSENT"


@dataclass(frozen=True, slots=True)
class ResolvedValue:
    source_name: str | None
    value: str | None
    status: str


@dataclass(frozen=True, slots=True)
class RailwayTwilioConfiguration:
    readiness: Readiness
    provider_status: str
    account_sid: ResolvedValue
    auth_token: ResolvedValue
    sender: ResolvedValue
    messaging_service_sid: ResolvedValue
    content_sid: ResolvedValue
    public_demo_flag: FlagStatus
    sender_mode: str
    message_mode: str
    masked_sender: str | None


def flag_status(environment: Mapping[str, str], name: str) -> FlagStatus:
    if name not in environment:
        return FlagStatus.ABSENT
    value = environment[name].strip().lower()
    if value in {"1", "true", "yes", "on"}:
        return FlagStatus.ENABLED
    if value in {"0", "false", "no", "off", ""}:
        return FlagStatus.DISABLED
    return FlagStatus.INVALID


def configured(environment: Mapping[str, str], name: str, pattern: re.Pattern[str] | None = None) -> ResolvedValue:
    value = environment.get(name, "").strip()
    if not value:
        return ResolvedValue(None, None, "ABSENT")
    if pattern is not None and not pattern.fullmatch(value):
        return ResolvedValue(name, None, "INVALID")
    return ResolvedValue(name, value, "PRESENT_VALID")


def normalize_sender(environment: Mapping[str, str]) -> ResolvedValue:
    name = "WHATSAPP_TWILIO_NUMBER"
    value = environment.get(name, "").strip()
    if not value:
        return ResolvedValue(None, None, "ABSENT")
    if value.lower().startswith("whatsapp:"):
        value = value[len("whatsapp:") :]
    try:
        return ResolvedValue(name, normalize_e164(value), "E164_VALID")
    except ValueError:
        return ResolvedValue(name, None, "INVALID")


def mask_phone(value: str) -> str:
    normalized = normalize_e164(value)
    return normalized[:3] + "*" * max(3, len(normalized) - 6) + normalized[-3:]


def resolve_railway_twilio_configuration(environment: Mapping[str, str]) -> RailwayTwilioConfiguration:
    provider = environment.get("WHATSAPP_PROVIDER", "").strip().lower()
    provider_status = "TWILIO" if provider == "twilio" else ("ABSENT" if not provider else "MISMATCH")
    account = configured(environment, "WHATSAPP_TWILIO_ACCOUNT_SID", ACCOUNT_SID)
    token = configured(environment, "WHATSAPP_TWILIO_AUTH_TOKEN")
    sender = normalize_sender(environment)
    messaging_service = configured(
        environment, "WHATSAPP_TWILIO_MESSAGING_SERVICE_SID", MESSAGING_SERVICE_SID
    )
    content = configured(environment, "WHATSAPP_TWILIO_CONTENT_SID", CONTENT_SID)
    public_flag = flag_status(environment, "SF_CONTACT_WHATSAPP_PUBLIC_DEMO_ENABLED")

    sender_ready = sender.value is not None or messaging_service.value is not None
    all_ready = all((account.value, token.value, sender_ready, content.value))
    if provider_status == "MISMATCH":
        readiness = Readiness.PROVIDER_MISMATCH
    elif provider_status == "ABSENT" and not any((account.value, token.value, sender_ready, content.value)):
        readiness = Readiness.UNAVAILABLE
    elif provider_status == "TWILIO" and all_ready:
        readiness = Readiness.READY
    else:
        readiness = Readiness.PARTIAL

    return RailwayTwilioConfiguration(
        readiness=readiness,
        provider_status=provider_status,
        account_sid=account,
        auth_token=token,
        sender=sender,
        messaging_service_sid=messaging_service,
        content_sid=content,
        public_demo_flag=public_flag,
        sender_mode="messaging_service" if messaging_service.value else ("direct_sender" if sender.value else "UNDETERMINED"),
        message_mode="BUSINESS_INITIATED_APPROVED_CONTENT" if content.value else "BUSINESS_INITIATED_CONTENT_NOT_CONFIGURED",
        masked_sender=mask_phone(sender.value) if sender.value else None,
    )


def safe_probe(configuration: RailwayTwilioConfiguration) -> dict[str, str | None]:
    return {
        "readiness": configuration.readiness.value,
        "provider_status": configuration.provider_status,
        "account_sid_status": configuration.account_sid.status,
        "auth_token_status": configuration.auth_token.status,
        "sender_status": configuration.sender.status,
        "messaging_service_status": configuration.messaging_service_sid.status,
        "content_sid_status": configuration.content_sid.status,
        "masked_sender": configuration.masked_sender,
        "public_demo_flag": configuration.public_demo_flag.value,
        "sender_mode": configuration.sender_mode,
        "message_mode": configuration.message_mode,
    }
