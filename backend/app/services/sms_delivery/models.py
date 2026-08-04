from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from enum import StrEnum

E164 = re.compile(r"^\+[1-9][0-9]{7,14}$", re.ASCII)
IDEMPOTENCY_KEY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{15,127}$", re.ASCII)
COUNTRY_CALLING_CODES = {"US": "1", "GB": "44", "IE": "353"}
PREMIUM_PREFIXES = {"US": ("+1900",), "GB": ("+449",), "IE": ("+35315",)}

MESSAGE_CONTRACT_ID = "SITEFORMO_SMS_DEMO_NOTIFICATION_V1"
MESSAGE_CONTRACT_VERSION = "v1"


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


def render_balanced_message(first_name: str | None) -> str:
    name = normalize_first_name(first_name)
    greeting = f"Hello, {name}." if name else "Hello."
    return (
        f"{greeting}\n\n"
        "This is an example of a short SMS notification your future website "
        "could send to your customers.\n\n"
        "SiteFormo"
    )


def render_visitor_notification(first_name: str | None, enquiry: str) -> str:
    """Server-owned visitor copy; user text is validated but never echoed."""
    del enquiry
    return render_balanced_message(first_name)


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
