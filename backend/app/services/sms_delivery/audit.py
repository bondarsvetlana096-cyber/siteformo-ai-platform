from __future__ import annotations

from typing import Protocol

import redis.asyncio as redis

SMS_AUDIT_NAMESPACE = "sf:demo-sms:v1:audit"


class SmsAuditStore(Protocol):
    async def create(self, delivery_id: str, values: dict[str, str], ttl_seconds: int) -> None: ...
    async def finalize(self, delivery_id: str, values: dict[str, str]) -> None: ...
    async def apply_provider_status(
        self, message_sid_hash: str, provider_status: str,
        error_code: str, error_message: str, updated_at: int,
    ) -> bool: ...


APPLY_PROVIDER_STATUS_SCRIPT = """
local delivery_id = redis.call('GET', KEYS[1])
if not delivery_id then return {'UNKNOWN'} end
local audit_key = KEYS[2] .. delivery_id
if redis.call('EXISTS', audit_key) == 0 then return {'UNKNOWN'} end
local current = redis.call('HGET', audit_key, 'provider_status') or ''
local terminal = {delivered=true, failed=true, undelivered=true}
if terminal[current] then
  if current == ARGV[1] then return {'DUPLICATE'} end
  return {'TERMINAL'}
end
local rank = {accepted=1, queued=1, sending=2, sent=3}
if not terminal[ARGV[1]] and (rank[ARGV[1]] or 0) < (rank[current] or 0) then
  return {'STALE'}
end
local final_state = 'PROVIDER_ACCEPTED'
if ARGV[1] == 'delivered' then final_state = 'DELIVERED' end
if ARGV[1] == 'failed' then final_state = 'FAILED' end
if ARGV[1] == 'undelivered' then final_state = 'UNDELIVERED' end
redis.call('HSET', audit_key,
  'provider_status', ARGV[1], 'final_state', final_state,
  'provider_error_code', ARGV[2], 'provider_error_message', ARGV[3],
  'provider_updated_at', ARGV[4], 'updated_at', ARGV[4])
return {'UPDATED'}
"""


class RedisSmsAuditStore:
    """Hash-only SMS audit. Values are constructed by SmsDeliveryService."""

    def __init__(self, redis_url: str, namespace: str = SMS_AUDIT_NAMESPACE) -> None:
        if not redis_url or namespace != SMS_AUDIT_NAMESPACE:
            raise ValueError("sms_audit_not_configured")
        self.redis_url = redis_url
        self.namespace = namespace

    def client(self) -> redis.Redis[str]:
        return redis.Redis.from_url(self.redis_url, decode_responses=True)

    def key(self, delivery_id: str) -> str:
        return f"{self.namespace}:delivery:{delivery_id}"

    async def create(self, delivery_id: str, values: dict[str, str], ttl_seconds: int) -> None:
        client = self.client()
        try:
            created = await client.hsetnx(self.key(delivery_id), "delivery_id", delivery_id)
            if not created:
                raise RuntimeError("sms_audit_collision")
            await client.hset(self.key(delivery_id), mapping=values)
            await client.expire(self.key(delivery_id), ttl_seconds)
        finally:
            await client.aclose()

    async def finalize(self, delivery_id: str, values: dict[str, str]) -> None:
        client = self.client()
        try:
            if not await client.exists(self.key(delivery_id)):
                raise RuntimeError("sms_audit_missing")
            await client.hset(self.key(delivery_id), mapping=values)
            sid_hash = values.get("message_sid_hash", "")
            if sid_hash:
                ttl = await client.ttl(self.key(delivery_id))
                await client.set(
                    f"{self.namespace}:sid:{sid_hash}", delivery_id,
                    ex=max(1, ttl),
                )
        finally:
            await client.aclose()

    async def apply_provider_status(
        self, message_sid_hash: str, provider_status: str,
        error_code: str, error_message: str, updated_at: int,
    ) -> bool:
        client = self.client()
        try:
            result = await client.eval(
                APPLY_PROVIDER_STATUS_SCRIPT,
                2,
                f"{self.namespace}:sid:{message_sid_hash}",
                f"{self.namespace}:delivery:",
                provider_status,
                error_code,
                error_message,
                str(updated_at),
            )
            return str(result[0]) in {"UPDATED", "DUPLICATE", "STALE", "TERMINAL"}
        finally:
            await client.aclose()
