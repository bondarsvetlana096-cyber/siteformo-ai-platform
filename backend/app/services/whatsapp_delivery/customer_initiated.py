from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import secrets
import time
from dataclasses import dataclass
from enum import Enum
from typing import Mapping, Protocol
from urllib.parse import quote

import redis.asyncio as redis

from app.services.whatsapp_delivery.models import WhatsAppMessage, normalize_e164
from app.services.whatsapp_delivery.transport import TransportState, WhatsAppTransport

TRIGGER_PREFIX = "Start SiteFormo WhatsApp example "
TOKEN = re.compile(r"^[A-Za-z0-9_-]{22}$")
MESSAGE_SID = re.compile(r"^(SM|MM)[0-9a-fA-F]{32}$")
CONTROL = re.compile(r"[\x00-\x1f\x7f]")
NAMESPACE = "sf:demo-whatsapp:v2"
CHANNEL = "WHATSAPP"
HANDOFF_TTL_SECONDS = 900
AUDIT_TTL_SECONDS = 604800
QUOTA_LIMIT = 2


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def normalize_first_name(value: str | None) -> str | None:
    if value is None:
        return None
    value = value.strip()
    if not value:
        return None
    if len(value) > 100 or CONTROL.search(value):
        raise ValueError("invalid_first_name")
    return value


def render_session_reply(first_name: str | None) -> str:
    name = normalize_first_name(first_name)
    greeting = f"Hi {name}," if name else "Hello,"
    return (f"{greeting}\n\nThis is an example of how communication with your customers can look through "
            "the WhatsApp channel on your future website.\n\nFrom this point, the conversation can continue "
            "directly in WhatsApp.\n\nThere is nothing else you need to do here.\n\nThank you for your time.\n\nSiteFormo")


def render_starter_message(token: str) -> str:
    if not TOKEN.fullmatch(token):
        raise ValueError("invalid_handoff_reference")
    return f"{TRIGGER_PREFIX}{token}"


def parse_trigger(body: str) -> str | None:
    body = body.strip()
    if not body.startswith(TRIGGER_PREFIX):
        return None
    token = body[len(TRIGGER_PREFIX):]
    return token if TOKEN.fullmatch(token) else None


def validate_twilio_signature(url: str, params: Mapping[str, str], signature: str, auth_token: str) -> bool:
    if not signature or not auth_token:
        return False
    material = url + "".join(key + params[key] for key in sorted(params))
    expected = base64.b64encode(hmac.new(auth_token.encode(), material.encode(), hashlib.sha1).digest()).decode()
    return hmac.compare_digest(expected, signature)


@dataclass(frozen=True, slots=True)
class HandoffState:
    example_hash: str
    first_name: str | None


class QuotaClaim(str, Enum):
    CLAIMED = "claimed"
    DUPLICATE = "duplicate"
    EXHAUSTED = "quota_exhausted"


@dataclass(frozen=True, slots=True)
class InboundResult:
    outcome: str
    provider_call_count: int
    delivery_hash: str | None = None


class ExampleStore(Protocol):
    async def create_handoff(self, client_hash: str, example_hash: str, first_name: str | None) -> str: ...
    async def consume_handoff(self, token_hash: str) -> HandoffState | None: ...
    async def claim_inbound(self, sid_hash: str, example_hash: str, recipient_hash: str) -> QuotaClaim: ...
    async def finalize_accepted(self, sid_hash: str, example_hash: str, recipient_hash: str) -> int: ...
    async def finalize_failed(self, sid_hash: str, example_hash: str, recipient_hash: str) -> None: ...
    async def audit(self, delivery_hash: str, fields: Mapping[str, str]) -> None: ...


CLAIM_SCRIPT = """
if redis.call('GET', KEYS[1]) then return 'duplicate' end
local accepted=tonumber(redis.call('GET',KEYS[2]) or '0')
local pending=tonumber(redis.call('GET',KEYS[3]) or '0')
if accepted+pending >= tonumber(ARGV[1]) then
 redis.call('SET',KEYS[1],'quota_exhausted','EX',ARGV[2]); return 'quota_exhausted'
end
redis.call('SET',KEYS[1],'pending','EX',ARGV[2]); redis.call('INCR',KEYS[3]); redis.call('EXPIRE',KEYS[3],ARGV[2]); return 'claimed'
"""
FINALIZE_ACCEPTED_SCRIPT = """
if redis.call('GET',KEYS[1]) ~= 'pending' then return -1 end
local pending=tonumber(redis.call('DECR',KEYS[3])); if pending<=0 then redis.call('DEL',KEYS[3]) end
local accepted=redis.call('INCR',KEYS[2]); redis.call('SET',KEYS[1],'accepted'); return accepted
"""
FINALIZE_FAILED_SCRIPT = """
if redis.call('GET',KEYS[1]) ~= 'pending' then return 0 end
local pending=tonumber(redis.call('DECR',KEYS[2])); if pending<=0 then redis.call('DEL',KEYS[2]) end
redis.call('SET',KEYS[1],'failed','EX',ARGV[1]); return 1
"""


class RedisWhatsAppExampleStore:
    def __init__(self, redis_url: str, namespace: str = NAMESPACE) -> None:
        if not redis_url:
            raise ValueError("redis_required")
        self.redis_url, self.namespace = redis_url, namespace

    def client(self) -> redis.Redis[str]:
        return redis.Redis.from_url(self.redis_url, decode_responses=True)

    def quota_key(self, example_hash: str, recipient_hash: str) -> str:
        return f"{self.namespace}:quota:{CHANNEL}:{example_hash}:{recipient_hash}"

    def _keys(self, sid_hash: str, example_hash: str, recipient_hash: str) -> tuple[str, str, str]:
        quota = self.quota_key(example_hash, recipient_hash)
        return f"{self.namespace}:inbound:{sid_hash}", quota, f"{quota}:pending"

    async def create_handoff(self, client_hash: str, example_hash: str, first_name: str | None) -> str:
        client = self.client()
        try:
            rate_key = f"{self.namespace}:prepare-rate:{client_hash}:{int(time.time())//3600}"
            count = await client.incr(rate_key)
            if count == 1:
                await client.expire(rate_key, 3700)
            if count > 20:
                raise RuntimeError("prepare_rate_limited")
            payload = json.dumps({"example_hash": example_hash, "first_name": first_name}, separators=(",", ":"))
            for _ in range(5):
                token = secrets.token_urlsafe(16)
                if TOKEN.fullmatch(token) and await client.set(
                    f"{self.namespace}:handoff:{digest(token)}", payload, ex=HANDOFF_TTL_SECONDS, nx=True
                ):
                    return token
            raise RuntimeError("handoff_reference_unavailable")
        finally:
            await client.aclose()

    async def consume_handoff(self, token_hash: str) -> HandoffState | None:
        client = self.client()
        try:
            raw = await client.getdel(f"{self.namespace}:handoff:{token_hash}")
        finally:
            await client.aclose()
        if not raw:
            return None
        payload = json.loads(raw)
        return HandoffState(str(payload["example_hash"]), normalize_first_name(payload.get("first_name")))

    async def claim_inbound(self, sid_hash: str, example_hash: str, recipient_hash: str) -> QuotaClaim:
        client = self.client()
        try:
            result = await client.eval(CLAIM_SCRIPT, 3, *self._keys(sid_hash, example_hash, recipient_hash),
                                       str(QUOTA_LIMIT), str(AUDIT_TTL_SECONDS))
            return QuotaClaim(str(result))
        finally:
            await client.aclose()

    async def finalize_accepted(self, sid_hash: str, example_hash: str, recipient_hash: str) -> int:
        client = self.client()
        try:
            return int(await client.eval(FINALIZE_ACCEPTED_SCRIPT, 3, *self._keys(sid_hash, example_hash, recipient_hash),
                                         str(AUDIT_TTL_SECONDS)))
        finally:
            await client.aclose()

    async def finalize_failed(self, sid_hash: str, example_hash: str, recipient_hash: str) -> None:
        sid, _quota, pending = self._keys(sid_hash, example_hash, recipient_hash)
        client = self.client()
        try:
            await client.eval(FINALIZE_FAILED_SCRIPT, 2, sid, pending, str(AUDIT_TTL_SECONDS))
        finally:
            await client.aclose()

    async def audit(self, delivery_hash: str, fields: Mapping[str, str]) -> None:
        allowed = {"inbound_sid_hash", "recipient_hash", "example_hash", "signature_valid", "session_window",
                   "transport_invoked", "provider_http_status", "provider_sid_present", "provider_sid_hash",
                   "typed_outcome", "provider_call_count", "timestamp"}
        client = self.client()
        try:
            key = f"{self.namespace}:audit:{delivery_hash}"
            await client.hset(key, mapping={k: v for k, v in fields.items() if k in allowed})
            await client.expire(key, AUDIT_TTL_SECONDS)
        finally:
            await client.aclose()


class CustomerInitiatedWhatsAppService:
    def __init__(self, store: ExampleStore, transport: WhatsAppTransport, sender_e164: str, public_base_url: str) -> None:
        self.store, self.transport = store, transport
        self.sender_e164, self.public_base_url = normalize_e164(sender_e164), public_base_url.rstrip("/")

    async def prepare(self, first_name: str | None, client_id: str, example_id: str) -> tuple[str, str]:
        token = await self.store.create_handoff(digest(client_id), digest(example_id), normalize_first_name(first_name))
        return f"https://wa.me/{self.sender_e164[1:]}?text={quote(render_starter_message(token))}", digest(token)

    async def handle_inbound(self, params: Mapping[str, str]) -> InboundResult:
        token = parse_trigger(params.get("Body", ""))
        if token is None:
            return InboundResult("IGNORED_UNAPPROVED_TRIGGER", 0)
        raw_from, raw_to = params.get("From", ""), params.get("To", "")
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
        handoff = await self.store.consume_handoff(digest(token))
        if handoff is None:
            return InboundResult("REJECTED_HANDOFF", 0)
        sid_hash, recipient_hash = digest(message_sid), digest(recipient)
        claim = await self.store.claim_inbound(sid_hash, handoff.example_hash, recipient_hash)
        if claim is QuotaClaim.DUPLICATE:
            return InboundResult("DUPLICATE", 0)
        if claim is QuotaClaim.EXHAUSTED:
            return InboundResult("QUOTA_EXHAUSTED", 0)
        delivery_hash = digest(f"{sid_hash}:{recipient_hash}:{handoff.example_hash}")
        reply = WhatsAppMessage(destination_e164=recipient, body=render_session_reply(handoff.first_name),
                                template_id="CUSTOMER_INITIATED_SESSION_FREEFORM", template_version="v2",
                                locale="en", correlation_id=delivery_hash, content_variables={})
        result = await self.transport.send(reply, sid_hash)
        if result.state is TransportState.ACCEPTED:
            await self.store.finalize_accepted(sid_hash, handoff.example_hash, recipient_hash)
        elif result.state not in {TransportState.TIMEOUT, TransportState.AMBIGUOUS_ACCEPTANCE}:
            await self.store.finalize_failed(sid_hash, handoff.example_hash, recipient_hash)
        await self.store.audit(delivery_hash, {
            "inbound_sid_hash": sid_hash, "recipient_hash": recipient_hash, "example_hash": handoff.example_hash,
            "signature_valid": "true", "session_window": "customer_initiated_open",
            "transport_invoked": str(result.transport_invoked).lower(),
            "provider_http_status": str(result.diagnostic.http_status or "") if result.diagnostic else "",
            "provider_sid_present": str(bool(result.provider_message_id)).lower(),
            "provider_sid_hash": digest(result.provider_message_id) if result.provider_message_id else "",
            "typed_outcome": result.state.value, "provider_call_count": "1" if result.transport_invoked else "0",
            "timestamp": str(int(time.time())),
        })
        if result.state is TransportState.ACCEPTED:
            return InboundResult("ACCEPTED", 1, delivery_hash)
        if result.state in {TransportState.TIMEOUT, TransportState.AMBIGUOUS_ACCEPTANCE}:
            return InboundResult("QUARANTINED", 1 if result.transport_invoked else 0, delivery_hash)
        return InboundResult("FAILED", 1 if result.transport_invoked else 0, delivery_hash)
