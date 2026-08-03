from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum

TOKEN = re.compile(r"^[A-Za-z0-9_-]{32,64}$", re.ASCII)
USERNAME = re.compile(r"^[A-Za-z][A-Za-z0-9_]{4,31}$", re.ASCII)

ROOT_NAMESPACE = "sf:demo-telegram:v1"
BINDING_NAMESPACE = f"{ROOT_NAMESPACE}:visitor-binding"
DELIVERY_NAMESPACE = f"{ROOT_NAMESPACE}:visitor-delivery"
OWNER_NAMESPACE = f"{ROOT_NAMESPACE}:owner-notification"
UPDATE_NAMESPACE = f"{ROOT_NAMESPACE}:update-dedup"


class BindingState(StrEnum):
    CREATED = "CREATED"
    CONSUMING = "CONSUMING"
    CONSUMED = "CONSUMED"
    DELIVERED = "DELIVERED"
    EXPIRED = "EXPIRED"
    REPLAY_BLOCKED = "REPLAY_BLOCKED"
    ORIGIN_MISMATCH = "ORIGIN_MISMATCH"
    INVALID_UPDATE = "INVALID_UPDATE"
    QUARANTINED = "QUARANTINED"


@dataclass(frozen=True, slots=True)
class DeepLinkResult:
    url: str
    expires_at: int
    binding_id: str


@dataclass(frozen=True, slots=True)
class TelegramStartUpdate:
    update_id: int
    chat_id: int
    token: str


@dataclass(frozen=True, slots=True)
class ConsumeResult:
    state: BindingState
    binding_id: str | None = None
    validated_name: str | None = None


@dataclass(frozen=True, slots=True)
class TelegramMessage:
    private_chat_id: int
    text: str
    correlation_id: str


def render_demo_message(name: str | None) -> str:
    validated = (name or "").strip()
    if validated:
        return (
            f"Hi {validated} — your Telegram connection is working. "
            "This is how your future website can send a helpful confirmation directly to a customer. "
            "SiteFormo"
        )
    return (
        "Your Telegram connection is working. "
        "This is how your future website can send a helpful confirmation directly to a customer. "
        "SiteFormo"
    )
