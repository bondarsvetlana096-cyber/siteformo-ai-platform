from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

import httpx
import redis.asyncio as redis

from app.services.contact_delivery.template import RenderedEmail

LOGGER = logging.getLogger(__name__)
CHANNEL = "EMAIL"
DELIVERY_LIMIT = 2
PENDING_LEASE_MS = 60_000
RATE_LIMIT_PER_HOUR = 20
STATE_PREFIX = "sf:demo-email:v1"
ORIGIN_EXAMPLE_REGISTRY = {
    "https://dev.siteformo.com": "SF_BU_01_CANONICAL_CONSULTING_EXAMPLE_V1",
    "https://business1.siteformo.com": "SF_BU_01_CANONICAL_CONSULTING_EXAMPLE_V1",
}

CLAIM_SCRIPT = """
local rate = redis.call('INCR', KEYS[4])
if rate == 1 then redis.call('EXPIRE', KEYS[4], ARGV[6]) end
if rate > tonumber(ARGV[5]) then return {'RATE_LIMITED'} end

local status = redis.call('HGET', KEYS[1], 'status')
if status then
  local stored_fingerprint = redis.call('HGET', KEYS[1], 'fingerprint')
  if stored_fingerprint ~= ARGV[1] then return {'CONFLICT'} end
  if status == 'accepted' then
    local remaining = redis.call('HGET', KEYS[1], 'remaining_deliveries')
    if not remaining then
      local accepted = tonumber(redis.call('HGET', KEYS[2], 'accepted') or '0')
      remaining = tostring(math.max(0, tonumber(ARGV[4]) - accepted))
    end
    return {'REPLAY_ACCEPTED', redis.call('HGET', KEYS[1], 'message_id') or '', remaining}
  end
  if status == 'pending' then
    local lease = redis.call('ZSCORE', KEYS[3], ARGV[2])
    if lease and tonumber(lease) > tonumber(ARGV[3]) then return {'IN_PROGRESS'} end
  end
end

redis.call('ZREMRANGEBYSCORE', KEYS[3], '-inf', ARGV[3])
local accepted = tonumber(redis.call('HGET', KEYS[2], 'accepted') or '0')
local pending = tonumber(redis.call('ZCARD', KEYS[3]) or '0')
if accepted + pending >= tonumber(ARGV[4]) then return {'QUOTA_EXHAUSTED'} end

redis.call('HSET', KEYS[1], 'status', 'pending', 'fingerprint', ARGV[1])
redis.call('ZADD', KEYS[3], ARGV[7], ARGV[2])
return {'ACQUIRED'}
"""

FINALIZE_ACCEPTED_SCRIPT = """
local status = redis.call('HGET', KEYS[1], 'status')
local stored_fingerprint = redis.call('HGET', KEYS[1], 'fingerprint')
if stored_fingerprint ~= ARGV[1] then return {'CONFLICT'} end
if status == 'accepted' then
  return {'REPLAY_ACCEPTED', redis.call('HGET', KEYS[1], 'message_id') or '', redis.call('HGET', KEYS[1], 'remaining_deliveries') or '0'}
end
if status ~= 'pending' then return {'INVALID_STATE'} end
redis.call('ZREM', KEYS[3], ARGV[2])
local accepted = redis.call('HINCRBY', KEYS[2], 'accepted', 1)
if accepted > tonumber(ARGV[4]) then
  redis.call('HINCRBY', KEYS[2], 'accepted', -1)
  return {'QUOTA_CONFLICT'}
end
local remaining = math.max(0, tonumber(ARGV[4]) - accepted)
redis.call('HSET', KEYS[1], 'status', 'accepted', 'message_id', ARGV[3], 'remaining_deliveries', tostring(remaining))
return {'ACCEPTED', tostring(accepted), tostring(remaining)}
"""

FINALIZE_FAILED_SCRIPT = """
local status = redis.call('HGET', KEYS[1], 'status')
local stored_fingerprint = redis.call('HGET', KEYS[1], 'fingerprint')
if stored_fingerprint ~= ARGV[1] then return {'CONFLICT'} end
if status == 'accepted' then return {'ACCEPTED'} end
redis.call('ZREM', KEYS[3], ARGV[2])
redis.call('HSET', KEYS[1], 'status', 'failed', 'failure_code', ARGV[3])
return {'FAILED'}
"""


class ClaimKind(StrEnum):
    ACQUIRED = "acquired"
    REPLAY_ACCEPTED = "replay_accepted"
    REPLAY_FAILED = "replay_failed"
    IN_PROGRESS = "in_progress"
    QUOTA_EXHAUSTED = "quota_exhausted"
    RATE_LIMITED = "rate_limited"
    CONFLICT = "conflict"


@dataclass(frozen=True)
class Claim:
    kind: ClaimKind
    provider_message_id: str | None = None
    failure_code: str | None = None
    remaining_deliveries: int | None = None


@dataclass(frozen=True)
class DeliveryIdentity:
    example_id: str
    recipient_hash: str
    idempotency_hash: str
    fingerprint: str
    client_hash: str


@dataclass(frozen=True)
class ProviderAcceptance:
    message_id: str
    http_status: int


class ProviderError(RuntimeError):
    def __init__(self, code: str, status_code: int = 502) -> None:
        super().__init__(code)
        self.code = code
        self.status_code = status_code


def enabled() -> bool:
    return os.getenv("SF_CONTACT_EMAIL_PUBLIC_DEMO_ENABLED", "false").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def trusted_example_for_origin(origin: str | None) -> str | None:
    return ORIGIN_EXAMPLE_REGISTRY.get(origin or "")


def request_fingerprint(payload: dict[str, str]) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def delivery_identity(
    *, example_id: str, recipient: str, idempotency_key: str, fingerprint: str, client_id: str
) -> DeliveryIdentity:
    return DeliveryIdentity(
        example_id=example_id,
        recipient_hash=hashlib.sha256(recipient.encode("utf-8")).hexdigest(),
        idempotency_hash=hashlib.sha256(idempotency_key.encode("utf-8")).hexdigest(),
        fingerprint=fingerprint,
        client_hash=hashlib.sha256(client_id.encode("utf-8")).hexdigest(),
    )


def _redis_client() -> redis.Redis[str]:
    redis_url = os.getenv("REDIS_URL")
    if not redis_url:
        raise RuntimeError("delivery_state_unavailable")
    return redis.Redis.from_url(redis_url, decode_responses=True)


def _keys(identity: DeliveryIdentity, now_seconds: int) -> tuple[str, str, str, str]:
    example_hash = hashlib.sha256(identity.example_id.encode("utf-8")).hexdigest()[:32]
    quota = f"{STATE_PREFIX}:quota:{CHANNEL}:{example_hash}:{identity.recipient_hash}"
    return (
        f"{STATE_PREFIX}:idem:{identity.idempotency_hash}",
        quota,
        f"{quota}:pending",
        f"{STATE_PREFIX}:rate:{identity.client_hash}:{now_seconds // 3600}",
    )


def _result_parts(result: object) -> list[str]:
    if not isinstance(result, (list, tuple)):
        raise TypeError("delivery_state_invalid")
    return [str(item) for item in result]


async def claim_once(identity: DeliveryIdentity) -> Claim:
    now_ms = int(time.time() * 1000)
    keys = _keys(identity, now_ms // 1000)
    client = _redis_client()
    try:
        result = _result_parts(
            await client.eval(  # type: ignore[no-untyped-call]
                CLAIM_SCRIPT,
                len(keys),
                *keys,
                identity.fingerprint,
                identity.idempotency_hash,
                str(now_ms),
                str(DELIVERY_LIMIT),
                str(RATE_LIMIT_PER_HOUR),
                "3700",
                str(now_ms + PENDING_LEASE_MS),
            )
        )
    finally:
        await client.close()
    code = result[0]
    mapping = {
        "ACQUIRED": ClaimKind.ACQUIRED,
        "IN_PROGRESS": ClaimKind.IN_PROGRESS,
        "QUOTA_EXHAUSTED": ClaimKind.QUOTA_EXHAUSTED,
        "RATE_LIMITED": ClaimKind.RATE_LIMITED,
        "CONFLICT": ClaimKind.CONFLICT,
    }
    if code == "REPLAY_ACCEPTED":
        return Claim(
            ClaimKind.REPLAY_ACCEPTED,
            provider_message_id=result[1] if len(result) > 1 else None,
            remaining_deliveries=int(result[2]) if len(result) > 2 else 0,
        )
    if code == "REPLAY_FAILED":
        return Claim(ClaimKind.REPLAY_FAILED, failure_code=result[1] if len(result) > 1 else None)
    if code not in mapping:
        raise RuntimeError("delivery_state_invalid")
    return Claim(mapping[code])


async def finalize_accepted(identity: DeliveryIdentity, message_id: str) -> int:
    now_seconds = int(time.time())
    keys = _keys(identity, now_seconds)
    client = _redis_client()
    try:
        result = _result_parts(
            await client.eval(  # type: ignore[no-untyped-call]
                FINALIZE_ACCEPTED_SCRIPT,
                3,
                *keys[:3],
                identity.fingerprint,
                identity.idempotency_hash,
                message_id,
                str(DELIVERY_LIMIT),
            )
        )
    finally:
        await client.close()
    if result[0] not in {"ACCEPTED", "REPLAY_ACCEPTED"}:
        raise RuntimeError("delivery_state_conflict")
    if result[0] == "ACCEPTED" and len(result) > 2:
        return int(result[2])
    if len(result) > 2:
        return int(result[2])
    return 0


async def finalize_failed(identity: DeliveryIdentity, failure_code: str) -> None:
    now_seconds = int(time.time())
    keys = _keys(identity, now_seconds)
    client = _redis_client()
    try:
        result = _result_parts(
            await client.eval(  # type: ignore[no-untyped-call]
                FINALIZE_FAILED_SCRIPT,
                3,
                *keys[:3],
                identity.fingerprint,
                identity.idempotency_hash,
                failure_code,
            )
        )
    finally:
        await client.close()
    if result[0] not in {"FAILED", "ACCEPTED"}:
        raise RuntimeError("delivery_state_conflict")


async def send_with_resend(
    message: RenderedEmail, recipient: str, idempotency_key: str, example_id: str
) -> ProviderAcceptance:
    api_key = os.getenv("RESEND_API_KEY")
    if not api_key:
        raise ProviderError("provider_not_configured", 503)
    provider_key = hashlib.sha256(f"{example_id}:{idempotency_key}".encode()).hexdigest()
    request: dict[str, Any] = {
        "from": message.sender,
        "to": [recipient],
        "reply_to": message.reply_to,
        "subject": message.subject,
        "html": message.html,
        "text": message.text,
        "tags": [
            {"name": "example_id", "value": example_id},
            {"name": "message_category", "value": "demonstration_enquiry"},
        ],
    }
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(10.0)) as client:
            response = await client.post(
                "https://api.resend.com/emails",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                    "Idempotency-Key": provider_key,
                },
                json=request,
            )
    except httpx.TimeoutException as exc:
        raise ProviderError("provider_timeout", 504) from exc
    except httpx.HTTPError as exc:
        raise ProviderError("provider_unavailable", 503) from exc
    if response.status_code < 200 or response.status_code >= 300:
        raise ProviderError("provider_rejected", 502)
    try:
        body = response.json()
    except ValueError as exc:
        raise ProviderError("provider_response_unconfirmed", 502) from exc
    message_id = body.get("id") if isinstance(body, dict) else None
    if not isinstance(message_id, str) or not message_id.strip() or len(message_id) > 128:
        raise ProviderError("provider_response_unconfirmed", 502)
    LOGGER.info(
        "contact_email_public_demo provider_accepted example=%s operation=%s",
        example_id,
        hashlib.sha256(idempotency_key.encode("utf-8")).hexdigest()[:16],
    )
    return ProviderAcceptance(message_id=message_id.strip(), http_status=response.status_code)
