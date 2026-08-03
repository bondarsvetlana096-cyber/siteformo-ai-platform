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
AUDIT_NAMESPACE = f"{ROOT_NAMESPACE}:visitor-delivery-audit"


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
    return (
        "Welcome to SiteFormo Bot. "
        "You can learn how SiteFormo works, ask about website examples, explore packages, "
        "pricing and timelines, and understand the first steps of starting a website project."
    )
