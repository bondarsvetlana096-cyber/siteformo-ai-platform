from __future__ import annotations

import base64
import hashlib
import hmac
import re
import time
from dataclasses import dataclass
from typing import Mapping, Protocol
from urllib.parse import quote

import redis.asyncio as redis

from app.services.whatsapp_delivery.models import WhatsAppMessage, normalize_e164
from app.services.whatsapp_delivery.transport import TransportState, WhatsAppTransport

TRIGGER = "Start SiteFormo WhatsApp example."
NAMED_TRIGGER_PREFIX = "Start SiteFormo WhatsApp example. My name is "
MESSAGE_SID = re.compile(r"^(SM|MM)[0-9a-fA-F]{32}$")
CONTROL = re.compile(r"[\x00-\x1f\x7f]")
AUDIT_TTL_SECONDS = 604800


def digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def normalize_first_name(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    if not normalized:
        return None
    if len(normalized) > 100 or CONTROL.search(normalized):
        raise ValueError("invalid_first_name")
    return normalized


def render_session_reply(first_name: str | None) -> str:
    greeting = f"Hi {normalize_first_name(first_name)}," if normalize_first_name(first_name) else "Hello,"
    return (
        f"{greeting}\n\n"
        "This is an example of how communication with your customers can look through the WhatsApp "
        "channel on your future website.\n\n"
        "From this point, the conversation can continue directly in WhatsApp.\n\n"
        "There is nothing else you need to do here.\n\n"
        "Thank you for your time.\n\n"
        "SiteFormo"
    )


def is_safe_user_authored_name(value: str) -> bool:
    return bool(value) and all(character.isalpha() or character in {" ", "-", "'", "’"} for character in value)


def render_starter_message(first_name: str | None) -> str:
    normalized = normalize_first_name(first_name)
    if normalized is None:
        return TRIGGER
    if not is_safe_user_authored_name(normalized):
        raise ValueError("invalid_first_name")
    return f"{NAMED_TRIGGER_PREFIX}{normalized}."


def parse_trigger(body: str) -> str | None | bool:
    normalized = body.strip()
    if normalized == TRIGGER:
        return None
    if not normalized.startswith(NAMED_TRIGGER_PREFIX) or not normalized.endswith("."):
        return False
    candidate = normalized[len(NAMED_TRIGGER_PREFIX) : -1]
    try:
        name = normalize_first_name(candidate)
    except ValueError:
        return None
    if name is None or not is_safe_user_authored_name(name):
        return None
    return name if render_starter_message(name) == normalized else False


def validate_twilio_signature(url: str, params: Mapping[str, str], signature: str, auth_token: str) -> bool:
    if not signature or not auth_token:
        return False
    material = url + "".join(key + params[key] for key in sorted(params))
    expected = base64.b64encode(hmac.new(auth_token.encode(), material.encode(), hashlib.sha1).digest()).decode()
    return hmac.compare_digest(expected, signature)


@dataclass(frozen=True, slots=True)
class InboundResult:
    outcome: str
    provider_call_count: int
    delivery_hash: str | None = None


class ExampleStore(Protocol):
    async def claim_prepare(self, client_hash: str) -> None: ...
    async def claim_inbound(self, message_sid_hash: str, recipient_hash: str) -> bool: ...
    async def audit(self, delivery_hash: str, fields: Mapping[str, str]) -> None: ...


class RedisWhatsAppExampleStore:
    def __init__(self, redis_url: str, namespace: str = "sf:whatsapp-example:v1") -> None:
        if not redis_url:
            raise ValueError("redis_required")
        self.redis_url = redis_url
        self.namespace = namespace

    def client(self) -> redis.Redis[str]:
        return redis.Redis.from_url(self.redis_url, decode_responses=True)

    async def claim_prepare(self, client_hash: str) -> None:
        client = self.client()
        try:
            rate_key = f"{self.namespace}:prepare-rate:{client_hash}:{int(time.time()) // 3600}"
            count = await client.incr(rate_key)
            if count == 1:
                await client.expire(rate_key, 3700)
            if count > 20:
                raise RuntimeError("prepare_rate_limited")
        finally:
            await client.aclose()

    async def claim_inbound(self, message_sid_hash: str, recipient_hash: str) -> bool:
        client = self.client()
        try:
            duplicate = await client.set(
                f"{self.namespace}:inbound:{message_sid_hash}", "claimed", ex=AUDIT_TTL_SECONDS, nx=True
            )
            if not duplicate:
                return False
            quota_key = f"{self.namespace}:quota:{recipient_hash}:{int(time.time()) // 86400}"
            count = await client.incr(quota_key)
            if count == 1:
                await client.expire(quota_key, 90000)
            return count <= 2
        finally:
            await client.aclose()

    async def audit(self, delivery_hash: str, fields: Mapping[str, str]) -> None:
        allowed = {
            "inbound_sid_hash", "recipient_hash", "trigger_kind", "signature_valid",
            "session_window", "transport_invoked", "provider_http_status", "provider_sid_present",
            "provider_sid_hash", "typed_outcome", "provider_call_count", "timestamp",
        }
        safe = {key: value for key, value in fields.items() if key in allowed}
        client = self.client()
        try:
            key = f"{self.namespace}:audit:{delivery_hash}"
            await client.hset(key, mapping=safe)
            await client.expire(key, AUDIT_TTL_SECONDS)
        finally:
            await client.aclose()


class CustomerInitiatedWhatsAppService:
    def __init__(
        self,
        store: ExampleStore,
        transport: WhatsAppTransport,
        sender_e164: str,
        public_base_url: str,
    ) -> None:
        self.store = store
        self.transport = transport
        self.sender_e164 = normalize_e164(sender_e164)
        self.public_base_url = public_base_url.rstrip("/")

    async def prepare(self, first_name: str | None, client_id: str) -> tuple[str, str]:
        name = normalize_first_name(first_name)
        text = render_starter_message(name)
        await self.store.claim_prepare(digest(client_id))
        url = f"https://wa.me/{self.sender_e164[1:]}?text={quote(text)}"
        return url, digest(text)

    async def handle_inbound(self, params: Mapping[str, str]) -> InboundResult:
        body = params.get("Body", "")
        parsed = parse_trigger(body)
        if parsed is False:
            return InboundResult("IGNORED_UNAPPROVED_TRIGGER", 0)
        raw_from = params.get("From", "")
        raw_to = params.get("To", "")
        if not raw_from.startswith("whatsapp:") or not raw_to.startswith("whatsapp:"):
            return InboundResult("REJECTED_INVALID_ADDRESS", 0)
        try:
            recipient = normalize_e164(raw_from.removeprefix("whatsapp:"))
            target = normalize_e164(raw_to.removeprefix("whatsapp:"))
        except ValueError:
            return InboundResult("REJECTED_INVALID_ADDRESS", 0)
        if target != self.sender_e164:
            return InboundResult("REJECTED_SENDER_BINDING", 0)
        message_sid = params.get("MessageSid", "")
        if not MESSAGE_SID.fullmatch(message_sid):
            return InboundResult("REJECTED_MESSAGE_ID", 0)

        sid_hash = digest(message_sid)
        recipient_hash = digest(recipient)
        if not await self.store.claim_inbound(sid_hash, recipient_hash):
            return InboundResult("DUPLICATE_OR_QUOTA", 0)

        delivery_hash = digest(f"{sid_hash}:{recipient_hash}")
        name: str | None = parsed if isinstance(parsed, str) else None
        trigger_kind = "neutral"
        if name is not None:
            trigger_kind = "user_authored_name"

        reply = WhatsAppMessage(
            destination_e164=recipient,
            body=render_session_reply(name),
            template_id="CUSTOMER_INITIATED_SESSION_FREEFORM",
            template_version="v1",
            locale="en",
            correlation_id=delivery_hash,
            content_variables={},
        )
        result = await self.transport.send(reply, sid_hash)
        provider_sid_hash = digest(result.provider_message_id) if result.provider_message_id else ""
        fields = {
            "inbound_sid_hash": sid_hash,
            "recipient_hash": recipient_hash,
            "trigger_kind": trigger_kind,
            "signature_valid": "true",
            "session_window": "customer_initiated_open",
            "transport_invoked": str(result.transport_invoked).lower(),
            "provider_http_status": str(result.diagnostic.http_status or ""),
            "provider_sid_present": str(bool(result.provider_message_id)).lower(),
            "provider_sid_hash": provider_sid_hash,
            "typed_outcome": result.state.value,
            "provider_call_count": "1" if result.transport_invoked else "0",
            "timestamp": str(int(time.time())),
        }
        await self.store.audit(delivery_hash, fields)
        if result.state is TransportState.ACCEPTED:
            return InboundResult("ACCEPTED", 1, delivery_hash)
        if result.state in {TransportState.TIMEOUT, TransportState.AMBIGUOUS_ACCEPTANCE}:
            return InboundResult("QUARANTINED", 1 if result.transport_invoked else 0, delivery_hash)
        return InboundResult("FAILED", 1 if result.transport_invoked else 0, delivery_hash)
