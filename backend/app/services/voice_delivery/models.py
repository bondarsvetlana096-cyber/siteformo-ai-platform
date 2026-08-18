from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass
from enum import StrEnum

E164 = re.compile(r"^\+[1-9][0-9]{7,14}$", re.ASCII)
IDEMPOTENCY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{15,127}$", re.ASCII)
COUNTRY_CODES = {"IE": "353"}


class VoiceState(StrEnum):
    REQUESTED = "REQUESTED"
    DELAYED = "DELAYED"
    PROVIDER_SUBMITTED = "PROVIDER_SUBMITTED"
    RINGING = "RINGING"
    ANSWERED = "ANSWERED"
    COMPLETED = "COMPLETED"
    BUSY = "BUSY"
    NO_ANSWER = "NO_ANSWER"
    FAILED = "FAILED"
    CANCELED = "CANCELED"
    PROVIDER_REJECTED = "PROVIDER_REJECTED"
    QUOTA_BLOCKED = "QUOTA_BLOCKED"
    DUPLICATE_SUPPRESSED = "DUPLICATE_SUPPRESSED"
    TIMEOUT_QUARANTINED = "TIMEOUT_QUARANTINED"


def normalize_name(value: str) -> str:
    candidate = " ".join(unicodedata.normalize("NFKC", value).strip().split())
    if not candidate or len(candidate) > 40:
        raise ValueError("invalid_first_name")
    if any(unicodedata.category(char).startswith("C") for char in candidate):
        raise ValueError("invalid_first_name")
    return candidate


def validate_phone(value: str, allowed_countries: frozenset[str]) -> str:
    candidate = value.strip()
    if not E164.fullmatch(candidate):
        raise ValueError("invalid_e164_phone")
    if not any(candidate.startswith("+" + COUNTRY_CODES[c]) for c in allowed_countries):
        raise ValueError("voice_country_not_allowed")
    if candidate.startswith("+35315"):
        raise ValueError("voice_premium_destination_blocked")
    return candidate


def validate_idempotency(value: str) -> str:
    if not IDEMPOTENCY.fullmatch(value):
        raise ValueError("invalid_idempotency_key")
    return value


def digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class VoiceRequest:
    request_id: str
    example_hash: str
    first_name: str
    phone_e164: str
    recipient_hash: str
    idempotency_hash: str
    client_hash: str
    scheduled_at: int


@dataclass(frozen=True, slots=True)
class ScheduleResult:
    state: VoiceState
    request_id: str
    scheduled_at: int
    replayed: bool = False


@dataclass(frozen=True, slots=True)
class ProviderResult:
    state: VoiceState
    http_status: int | None = None
    call_sid: str | None = None
    transport_invoked: bool = True
