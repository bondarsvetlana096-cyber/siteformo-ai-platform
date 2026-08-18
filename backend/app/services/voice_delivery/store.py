from __future__ import annotations

from typing import Protocol

import redis.asyncio as redis

from app.services.voice_delivery.models import ScheduleResult, VoiceRequest, VoiceState, digest

NAMESPACE = "sf:demo-voice:v1"

SCHEDULE_SCRIPT = """
local idem = KEYS[1]
if redis.call('EXISTS', idem) == 1 then
  local fingerprint = redis.call('HGET', idem, 'fingerprint') or ''
  if fingerprint ~= ARGV[1] then return {'CONFLICT'} end
  return {'DUPLICATE', redis.call('HGET', idem, 'request_id') or '', redis.call('HGET', idem, 'scheduled_at') or '0'}
end
local recipient_used = tonumber(redis.call('GET', KEYS[2]) or '0')
local global_used = tonumber(redis.call('GET', KEYS[3]) or '0')
local client_used = tonumber(redis.call('GET', KEYS[4]) or '0')
if recipient_used >= tonumber(ARGV[8]) then return {'RECIPIENT_QUOTA'} end
if global_used >= tonumber(ARGV[9]) then return {'GLOBAL_QUOTA'} end
if client_used >= tonumber(ARGV[10]) then return {'RATE_LIMITED'} end
redis.call('HSET', idem,
  'fingerprint', ARGV[1], 'request_id', ARGV[2], 'state', 'DELAYED',
  'scheduled_at', ARGV[3], 'first_name', ARGV[4], 'phone', ARGV[5],
  'recipient_hash', ARGV[6], 'provider_call_count', '0')
redis.call('EXPIRE', idem, ARGV[7])
redis.call('SET', KEYS[2], tostring(recipient_used + 1), 'EX', ARGV[7])
redis.call('SET', KEYS[3], tostring(global_used + 1), 'EX', '3600')
redis.call('SET', KEYS[4], tostring(client_used + 1), 'EX', '3600')
redis.call('ZADD', KEYS[5], ARGV[3], ARGV[2])
redis.call('SET', KEYS[6], ARGV[11], 'EX', ARGV[7])
return {'SCHEDULED', ARGV[2], ARGV[3]}
"""

CLAIM_DUE_SCRIPT = """
local values = redis.call('ZRANGEBYSCORE', KEYS[1], '-inf', ARGV[1], 'LIMIT', 0, 1)
if #values == 0 then return {} end
local request_id = values[1]
redis.call('ZREM', KEYS[1], request_id)
local idem_hash = redis.call('GET', KEYS[2] .. request_id)
if not idem_hash then return {} end
local record = KEYS[3] .. idem_hash
if redis.call('HGET', record, 'state') ~= 'DELAYED' then return {} end
local first_name = redis.call('HGET', record, 'first_name') or ''
local phone = redis.call('HGET', record, 'phone') or ''
local recipient_hash = redis.call('HGET', record, 'recipient_hash') or ''
local scheduled_at = redis.call('HGET', record, 'scheduled_at') or '0'
redis.call('HSET', record, 'state', 'TIMEOUT_QUARANTINED', 'claimed_at', ARGV[1])
redis.call('HDEL', record, 'first_name', 'phone')
return {request_id, idem_hash, first_name, phone, recipient_hash, scheduled_at}
"""


class VoiceStore(Protocol):
    async def schedule(self, request: VoiceRequest, *, fingerprint: str) -> ScheduleResult: ...
    async def claim_due(self, now_seconds: int) -> VoiceRequest | None: ...
    async def finalize_submission(self, request: VoiceRequest, state: VoiceState, call_sid_hash: str) -> None: ...
    async def apply_callback(self, call_sid_hash: str, state: VoiceState, timestamp: int) -> bool: ...


class RedisVoiceStore:
    def __init__(self, redis_url: str, *, recipient_limit: int = 2, global_limit: int = 5) -> None:
        if not redis_url:
            raise ValueError("voice_store_not_configured")
        self.redis_url = redis_url
        self.recipient_limit, self.global_limit = recipient_limit, global_limit

    def client(self):
        return redis.Redis.from_url(self.redis_url, decode_responses=True)

    async def schedule(self, request: VoiceRequest, *, fingerprint: str) -> ScheduleResult:
        idem = f"{NAMESPACE}:idem:{request.idempotency_hash}"
        hour = request.scheduled_at // 3600
        keys = [
            idem,
            f"{NAMESPACE}:quota:CALL:{request.example_hash}:{request.recipient_hash}",
            f"{NAMESPACE}:quota:global:{hour}",
            f"{NAMESPACE}:rate:{request.client_hash}:{hour}",
            f"{NAMESPACE}:delayed",
            f"{NAMESPACE}:request-map:{request.request_id}",
        ]
        connection = self.client()
        try:
            raw = await connection.eval(
                SCHEDULE_SCRIPT, len(keys), *keys, fingerprint, request.request_id,
                str(request.scheduled_at), request.first_name, request.phone_e164,
                request.recipient_hash, "86400", str(self.recipient_limit),
                str(self.global_limit), "5", request.idempotency_hash,
            )
        finally:
            await connection.aclose()
        code = str(raw[0])
        if code == "SCHEDULED":
            return ScheduleResult(VoiceState.DELAYED, str(raw[1]), int(raw[2]))
        if code == "DUPLICATE":
            return ScheduleResult(VoiceState.DUPLICATE_SUPPRESSED, str(raw[1]), int(raw[2]), True)
        if code == "CONFLICT":
            raise ValueError("voice_idempotency_conflict")
        if code in {"RECIPIENT_QUOTA", "GLOBAL_QUOTA"}:
            raise PermissionError("voice_quota_exhausted")
        if code == "RATE_LIMITED":
            raise PermissionError("voice_rate_limited")
        raise RuntimeError("voice_state_unavailable")

    async def claim_due(self, now_seconds: int) -> VoiceRequest | None:
        connection = self.client()
        try:
            raw = await connection.eval(
                CLAIM_DUE_SCRIPT, 3, f"{NAMESPACE}:delayed",
                f"{NAMESPACE}:request-map:", f"{NAMESPACE}:idem:", str(now_seconds),
            )
        finally:
            await connection.aclose()
        if not raw:
            return None
        request_id, idem_hash, name, phone, recipient_hash, scheduled_at = map(str, raw)
        return VoiceRequest(request_id, "", name, phone, recipient_hash, idem_hash, "", int(scheduled_at))

    async def finalize_submission(self, request: VoiceRequest, state: VoiceState, call_sid_hash: str) -> None:
        key = f"{NAMESPACE}:idem:{request.idempotency_hash}"
        connection = self.client()
        try:
            await connection.hset(key, mapping={
                "state": state.value, "call_sid_hash": call_sid_hash,
                "provider_call_count": "1", "phone": "", "first_name": "",
            })
            if call_sid_hash:
                await connection.set(f"{NAMESPACE}:call:{call_sid_hash}", request.idempotency_hash, ex=86400)
        finally:
            await connection.aclose()

    async def apply_callback(self, call_sid_hash: str, state: VoiceState, timestamp: int) -> bool:
        connection = self.client()
        try:
            idem_hash = await connection.get(f"{NAMESPACE}:call:{call_sid_hash}")
            if not idem_hash:
                return False
            await connection.hset(f"{NAMESPACE}:idem:{idem_hash}", mapping={
                "state": state.value, "callback_at": str(timestamp),
            })
            return True
        finally:
            await connection.aclose()
