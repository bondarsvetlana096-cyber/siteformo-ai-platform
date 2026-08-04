from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from enum import StrEnum
from math import ceil

E164 = re.compile(r"^\+[1-9][0-9]{7,14}$", re.ASCII)
IDEMPOTENCY_KEY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{15,127}$", re.ASCII)
COUNTRY_CALLING_CODES = {"US": "1", "GB": "44", "IE": "353"}
PREMIUM_PREFIXES = {"US": ("+1900",), "GB": ("+449",), "IE": ("+35315",)}

MESSAGE_CONTRACT_ID = "SITEFORMO_SMS_DEMO_NOTIFICATION_V1"
MESSAGE_CONTRACT_VERSION = "v1"

GSM7_BASIC = frozenset(
    "@£$¥èéùìòÇ\nØø\rÅåΔ_ΦΓΛΩΠΨΣΘΞÆæßÉ !\"#¤%&'()*+,-./0123456789:;<=>?"
    "¡ABCDEFGHIJKLMNOPQRSTUVWXYZÄÖÑÜ§¿abcdefghijklmnopqrstuvwxyzäöñüà"
)
GSM7_EXTENSION = frozenset("^{}\\[~]|€")


class SMSDeliveryMode(StrEnum):
    VISITOR_NOTIFICATION = "VISITOR_NOTIFICATION"
    OWNER_ALERT = "OWNER_ALERT"
    BOTH = "BOTH"


class SMSDeliveryRole(StrEnum):
    VISITOR = "VISITOR"
    OWNER = "OWNER"


def normalize_e164(value: str) -> str:
    normalized = value.strip()
    if not E164.fullmatch(normalized):
        raise ValueError("invalid_e164_phone")
    return normalized


def validate_destination(value: str, allowed_countries: frozenset[str]) -> str:
    normalized = normalize_e164(value)
    matches = [country for country in allowed_countries if normalized.startswith("+" + COUNTRY_CALLING_CODES[country])]
    if not matches:
        raise ValueError("sms_country_not_allowed")
    country = max(matches, key=lambda item: len(COUNTRY_CALLING_CODES[item]))
    if normalized.startswith(PREMIUM_PREFIXES.get(country, ())):
        raise ValueError("sms_premium_destination_blocked")
    return normalized


def normalize_first_name(value: str | None) -> str:
    if not value:
        return ""
    candidate = " ".join(unicodedata.normalize("NFKC", value).strip().split())
    if len(candidate) > 40 or any(unicodedata.category(char).startswith("C") for char in candidate):
        raise ValueError("invalid_first_name")
    return candidate


def normalize_message(value: str | None, *, required: bool) -> str:
    candidate = " ".join(unicodedata.normalize("NFKC", value or "").strip().split())
    if required and not candidate:
        raise ValueError("sms_message_required")
    if len(candidate) > 240 or any(unicodedata.category(char).startswith("C") for char in candidate):
        raise ValueError("invalid_sms_message")
    return candidate


@dataclass(frozen=True, slots=True)
class SmsSegmentInfo:
    encoding: str
    units: int
    segment_count: int
    single_segment_limit: int


def analyze_sms_segments(body: str) -> SmsSegmentInfo:
    if all(char in GSM7_BASIC or char in GSM7_EXTENSION for char in body):
        units = sum(2 if char in GSM7_EXTENSION else 1 for char in body)
        return SmsSegmentInfo("GSM-7", units, 1 if units <= 160 else ceil(units / 153), 160)
    units = len(body.encode("utf-16-be")) // 2
    return SmsSegmentInfo("UCS-2", units, 1 if units <= 70 else ceil(units / 67), 70)


def validate_user_message(value: str | None) -> str:
    if value is None:
        raise ValueError("sms_message_required")
    candidate = value.strip()
    if not candidate:
        raise ValueError("sms_message_required")
    if "\r" in candidate or "\n" in candidate:
        raise ValueError("invalid_sms_message")
    if any(unicodedata.category(char).startswith("C") for char in candidate):
        raise ValueError("invalid_sms_message")
    return candidate


def validate_idempotency_key(value: str) -> str:
    if not IDEMPOTENCY_KEY.fullmatch(value):
        raise ValueError("invalid_idempotency_key")
    return value


@dataclass(frozen=True, slots=True)
class SmsMessage:
    destination_e164: str
    body: str
    contract_id: str = MESSAGE_CONTRACT_ID
    contract_version: str = MESSAGE_CONTRACT_VERSION


def render_visitor_notification(first_name: str | None, enquiry: str) -> str:
    """Build the provider body from validated input under a one-segment policy."""
    name = normalize_first_name(first_name)
    message = validate_user_message(enquiry)
    body = f"{name}: {message}" if name else message
    if analyze_sms_segments(body).segment_count != 1:
        raise ValueError("sms_message_too_long")
    return body


def render_owner_alert(first_name: str | None, visitor_contact: str | None, enquiry: str) -> str:
    name = normalize_first_name(first_name) or "Not provided"
    message = normalize_message(enquiry, required=True)
    lines = ["New website enquiry.", f"Name: {name}"]
    if visitor_contact:
        lines.append(f"Contact: {normalize_e164(visitor_contact)}")
    lines.append(f"Message: {message}")
    body = "\n".join(lines)
    if len(body) > 320:
        raise ValueError("sms_owner_alert_too_long")
    return body
