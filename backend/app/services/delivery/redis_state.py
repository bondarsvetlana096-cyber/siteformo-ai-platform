from __future__ import annotations

import time

import redis.asyncio as redis

from app.services.delivery.contracts import Claim, ClaimKind, DeliveryIdentity

CLAIM_SCRIPT = """
local rate = redis.call('INCR', KEYS[4])
if rate == 1 then redis.call('EXPIRE', KEYS[4], ARGV[6]) end
if rate > tonumber(ARGV[5]) then return {'RATE_LIMITED'} end
local status = redis.call('HGET', KEYS[1], 'status')
if status then
  local stored = redis.call('HGET', KEYS[1], 'fingerprint')
  if stored ~= ARGV[1] then return {'CONFLICT'} end
  if status == 'accepted' then return {'REPLAY_ACCEPTED', redis.call('HGET', KEYS[1], 'message_id') or '', redis.call('HGET', KEYS[1], 'remaining_deliveries') or '0'} end
  if status == 'quarantined' then return {'REPLAY_QUARANTINED', redis.call('HGET', KEYS[1], 'failure_code') or 'provider_outcome_ambiguous'} end
  if status == 'pending' then
    local lease = redis.call('ZSCORE', KEYS[3], ARGV[2])
    if lease and tonumber(lease) > tonumber(ARGV[3]) then return {'IN_PROGRESS'} end
  end
end
redis.call('ZREMRANGEBYSCORE', KEYS[3], '-inf', ARGV[3])
local accepted = tonumber(redis.call('HGET', KEYS[2], 'accepted') or '0')
local quarantined = tonumber(redis.call('HGET', KEYS[2], 'quarantined') or '0')
local pending = tonumber(redis.call('ZCARD', KEYS[3]) or '0')
if accepted + quarantined + pending >= tonumber(ARGV[4]) then return {'QUOTA_EXHAUSTED'} end
redis.call('HSET', KEYS[1], 'status', 'pending', 'fingerprint', ARGV[1])
redis.call('ZADD', KEYS[3], ARGV[7], ARGV[2])
return {'ACQUIRED'}
"""

FINALIZE_SCRIPT = """
local stored = redis.call('HGET', KEYS[1], 'fingerprint')
if stored ~= ARGV[1] then return {'CONFLICT'} end
local status = redis.call('HGET', KEYS[1], 'status')
if status ~= 'pending' then return {'INVALID_STATE'} end
redis.call('ZREM', KEYS[3], ARGV[2])
if ARGV[3] == 'accepted' then
  local used = redis.call('HINCRBY', KEYS[2], 'accepted', 1)
  local quarantined = tonumber(redis.call('HGET', KEYS[2], 'quarantined') or '0')
  local remaining = math.max(0, tonumber(ARGV[6]) - used - quarantined)
  redis.call('HSET', KEYS[1], 'status', 'accepted', 'message_id', ARGV[4], 'remaining_deliveries', tostring(remaining))
  return {'ACCEPTED', tostring(remaining)}
end
if ARGV[3] == 'quarantined' then
  redis.call('HINCRBY', KEYS[2], 'quarantined', 1)
  redis.call('HSET', KEYS[1], 'status', 'quarantined', 'failure_code', ARGV[5])
  return {'QUARANTINED'}
end
redis.call('HSET', KEYS[1], 'status', 'failed', 'failure_code', ARGV[5])
return {'RELEASED'}
"""


class RedisDeliveryState:
    def __init__(self, redis_url: str, namespace: str, limit: int = 2) -> None:
        if not redis_url:
            raise ValueError("delivery_state_unavailable")
        self.redis_url = redis_url
        self.namespace = namespace
        self.limit = limit

    def keys(self, identity: DeliveryIdentity, now_seconds: int) -> tuple[str, str, str, str]:
        quota = f"{self.namespace}:quota:{identity.channel}:{identity.example_id}:{identity.recipient_hash}"
        return (
            f"{self.namespace}:idem:{identity.idempotency_hash}",
            quota,
            f"{quota}:pending",
            f"{self.namespace}:rate:{identity.client_hash}:{now_seconds // 3600}",
        )

    def client(self) -> redis.Redis[str]:
        return redis.Redis.from_url(self.redis_url, decode_responses=True)

    async def claim(self, identity: DeliveryIdentity) -> Claim:
        now_ms = int(time.time() * 1000)
        keys = self.keys(identity, now_ms // 1000)
        client = self.client()
        try:
            raw = await client.eval(CLAIM_SCRIPT, 4, *keys, identity.fingerprint, identity.idempotency_hash, str(now_ms), str(self.limit), "20", "3700", str(now_ms + 60_000))
        finally:
            await client.aclose()
        result = [str(item) for item in raw]
        if result[0] == "REPLAY_ACCEPTED":
            return Claim(ClaimKind.REPLAY_ACCEPTED, result[1], int(result[2]))
        if result[0] == "REPLAY_QUARANTINED":
            return Claim(ClaimKind.REPLAY_QUARANTINED, failure_code=result[1])
        return Claim(ClaimKind(result[0].lower()))

    async def _finalize(self, identity: DeliveryIdentity, state: str, message_id: str = "", code: str = "") -> list[str]:
        keys = self.keys(identity, int(time.time()))
        client = self.client()
        try:
            raw = await client.eval(FINALIZE_SCRIPT, 3, *keys[:3], identity.fingerprint, identity.idempotency_hash, state, message_id, code, str(self.limit))
        finally:
            await client.aclose()
        return [str(item) for item in raw]

    async def accept(self, identity: DeliveryIdentity, provider_message_id: str) -> int:
        result = await self._finalize(identity, "accepted", provider_message_id)
        if result[0] != "ACCEPTED":
            raise RuntimeError("delivery_state_conflict")
        return int(result[1])

    async def release(self, identity: DeliveryIdentity, failure_code: str) -> None:
        if (await self._finalize(identity, "released", code=failure_code))[0] != "RELEASED":
            raise RuntimeError("delivery_state_conflict")

    async def quarantine(self, identity: DeliveryIdentity, failure_code: str) -> None:
        if (await self._finalize(identity, "quarantined", code=failure_code))[0] != "QUARANTINED":
            raise RuntimeError("delivery_state_conflict")
