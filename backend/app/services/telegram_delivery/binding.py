from __future__ import annotations

import secrets
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Mapping, Protocol

import redis.asyncio as redis

from app.services.telegram_delivery.models import (
    BINDING_NAMESPACE,
    UPDATE_NAMESPACE,
    BindingState,
    ConsumeResult,
    DeepLinkResult,
    USERNAME,
)
from app.services.telegram_delivery.security import private_id_hash, token_hash

CREATE_SCRIPT = """
if redis.call('EXISTS', KEYS[1]) == 1 then return {'COLLISION'} end
redis.call('HSET', KEYS[1],
  'status', 'CREATED', 'binding_id', ARGV[1], 'example_hash', ARGV[2],
  'origin_hash', ARGV[3], 'name', ARGV[4], 'message', ARGV[5], 'expires_at', ARGV[6])
redis.call('EXPIRE', KEYS[1], ARGV[7])
return {'CREATED'}
"""

CONSUME_SCRIPT = """
local dedupe = redis.call('SET', KEYS[2], '1', 'NX', 'EX', ARGV[4])
if not dedupe then return {'DUPLICATE_UPDATE'} end
if redis.call('EXISTS', KEYS[1]) == 0 then return {'EXPIRED'} end
local expires = tonumber(redis.call('HGET', KEYS[1], 'expires_at') or '0')
if expires <= tonumber(ARGV[1]) then
  redis.call('HSET', KEYS[1], 'status', 'EXPIRED')
  return {'EXPIRED'}
end
local status = redis.call('HGET', KEYS[1], 'status')
local stored_chat = redis.call('HGET', KEYS[1], 'chat_hash') or ''
if status ~= 'CREATED' then
  if stored_chat ~= '' and stored_chat ~= ARGV[2] then return {'CHAT_CONFLICT'} end
  return {'REPLAY_BLOCKED'}
end
redis.call('HSET', KEYS[1], 'status', 'CONSUMING')
redis.call('HSET', KEYS[1], 'chat_hash', ARGV[2], 'status', 'CONSUMED')
return {'CONSUMED', redis.call('HGET', KEYS[1], 'binding_id') or '', redis.call('HGET', KEYS[1], 'name') or '', redis.call('HGET', KEYS[1], 'message') or ''}
"""

FINALIZE_SCRIPT = """
local status = redis.call('HGET', KEYS[1], 'status')
if status ~= 'CONSUMED' then return {'INVALID_STATE'} end
redis.call('HSET', KEYS[1], 'status', ARGV[1], 'provider_reference_hash', ARGV[2])
return {ARGV[1]}
"""


class BindingStore(Protocol):
    async def create(
        self, *, token_digest: str, binding_id: str, example_hash: str, origin_hash: str,
        validated_name: str, validated_message: str, expires_at: int, ttl_seconds: int
    ) -> None: ...

    async def consume(
        self, *, token_digest: str, update_digest: str, chat_digest: str, now_seconds: int
    ) -> ConsumeResult: ...

    async def finalize(
        self, *, token_digest: str, state: BindingState, provider_reference_hash: str
    ) -> None: ...


class RedisTelegramBindingStore:
    def __init__(self, redis_url: str, namespace: str = BINDING_NAMESPACE) -> None:
        if not redis_url or not namespace.startswith("sf:demo-telegram:v1:visitor-binding"):
            raise ValueError("telegram_binding_store_not_configured")
        self.redis_url, self.namespace = redis_url, namespace

    def client(self) -> redis.Redis[str]:
        return redis.Redis.from_url(self.redis_url, decode_responses=True)

    def binding_key(self, digest: str) -> str:
        return f"{self.namespace}:token:{digest}"

    @staticmethod
    def update_key(digest: str) -> str:
        return f"{UPDATE_NAMESPACE}:update:{digest}"

    async def create(self, **values: object) -> None:
        client = self.client()
        try:
            raw = await client.eval(
                CREATE_SCRIPT, 1, self.binding_key(str(values["token_digest"])),
                values["binding_id"], values["example_hash"], values["origin_hash"],
                values["validated_name"], values["validated_message"], values["expires_at"],
                values["ttl_seconds"],
            )
        finally:
            await client.aclose()
        if str(raw[0]) != "CREATED":
            raise RuntimeError("binding_token_collision")

    async def consume(self, **values: object) -> ConsumeResult:
        client = self.client()
        try:
            raw = await client.eval(
                CONSUME_SCRIPT, 2, self.binding_key(str(values["token_digest"])),
                self.update_key(str(values["update_digest"])), values["now_seconds"],
                values["chat_digest"], values["token_digest"], "86400",
            )
        finally:
            await client.aclose()
        code = str(raw[0])
        if code == "CONSUMED":
            return ConsumeResult(
                BindingState.CONSUMED, str(raw[1]), str(raw[2]) or None, str(raw[3]) or None
            )
        if code == "EXPIRED":
            return ConsumeResult(BindingState.EXPIRED)
        return ConsumeResult(BindingState.REPLAY_BLOCKED)

    async def finalize(self, **values: object) -> None:
        client = self.client()
        try:
            raw = await client.eval(
                FINALIZE_SCRIPT, 1, self.binding_key(str(values["token_digest"])),
                values["state"].value, values["provider_reference_hash"],
            )
        finally:
            await client.aclose()
        if str(raw[0]) != values["state"].value:
            raise RuntimeError("binding_finalize_conflict")


@dataclass(frozen=True, slots=True)
class TrustedExample:
    example_id: str
    exact_origin: str


class DeepLinkService:
    def __init__(
        self, *, store: BindingStore, bot_username: str, ttl_seconds: int,
        trusted_origins: Mapping[str, TrustedExample], clock: Callable[[], float] = time.time,
    ) -> None:
        username = bot_username.lstrip("@")
        if not USERNAME.fullmatch(username):
            raise ValueError("invalid_telegram_bot_username")
        if ttl_seconds < 60 or ttl_seconds > 3600:
            raise ValueError("binding_ttl_owner_approval_required")
        self.store, self.bot_username, self.ttl_seconds = store, username, ttl_seconds
        self.trusted_origins, self.clock = dict(trusted_origins), clock

    async def create(
        self, *, origin: str | None, validated_name: str | None,
        validated_message: str,
    ) -> DeepLinkResult:
        trusted = self.trusted_origins.get(origin or "")
        if trusted is None or trusted.exact_origin != origin:
            raise PermissionError(BindingState.ORIGIN_MISMATCH.value)
        name = (validated_name or "").strip()
        if len(name) > 100 or "\r" in name or "\n" in name:
            raise ValueError("invalid_name")
        message = validated_message.strip()
        if (
            not message or len(message) > 240 or "\r" in message or "\n" in message
            or any(ord(char) < 32 or ord(char) == 127 for char in message)
        ):
            raise ValueError("invalid_message")
        raw_token = secrets.token_urlsafe(32)
        binding_id = uuid.uuid4().hex
        expires_at = int(self.clock()) + self.ttl_seconds
        await self.store.create(
            token_digest=token_hash(raw_token), binding_id=binding_id,
            example_hash=private_id_hash(trusted.example_id), origin_hash=private_id_hash(origin),
            validated_name=name, validated_message=message,
            expires_at=expires_at, ttl_seconds=self.ttl_seconds,
        )
        return DeepLinkResult(
            f"https://t.me/{self.bot_username}?start={raw_token}", expires_at, binding_id
        )
