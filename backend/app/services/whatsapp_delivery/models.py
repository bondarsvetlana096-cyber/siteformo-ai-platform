from __future__ import annotations

import re
from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping

E164 = re.compile(r"^\+[1-9][0-9]{1,14}$", re.ASCII)
MESSAGE_CONTRACT_ID = "SITEFORMO_WHATSAPP_DEMONSTRATION_TEMPLATE_V1"
MESSAGE_CONTRACT_VERSION = "v1"
DEFAULT_TEMPLATE_FIRST_NAME = "there"
TEMPLATE_BODY = (
    "Hi {{1}},\n\n"
    "Your SiteFormo WhatsApp demonstration worked.\n\n"
    "This message was sent from the example website to the WhatsApp number you entered.\n\n"
    "Your website can deliver customer enquiries the same way.\n\n"
    "SiteFormo"
)


def normalize_e164(value: str) -> str:
    normalized = value.strip()
    if not E164.fullmatch(normalized):
        raise ValueError("invalid_e164_phone")
    return normalized


def twilio_whatsapp_destination(normalized_e164: str) -> str:
    return f"whatsapp:{normalize_e164(normalized_e164)}"


@dataclass(frozen=True, slots=True)
class WhatsAppMessage:
    destination_e164: str
    body: str
    template_id: str
    template_version: str
    locale: str
    correlation_id: str
    content_variables: Mapping[str, str]


def render_demo_message(name: str, correlation_id: str) -> WhatsAppMessage:
    body = TEMPLATE_BODY.replace("{{1}}", name)
    return WhatsAppMessage(
        "", body, MESSAGE_CONTRACT_ID, MESSAGE_CONTRACT_VERSION, "en", correlation_id,
        MappingProxyType({"first_name": name}),
    )
