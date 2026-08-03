from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
import hashlib
from typing import Protocol

import redis.asyncio as redis

from app.services.telegram_delivery.binding import DeepLinkService
from app.services.telegram_delivery.models import BindingState, DeepLinkResult
from app.services.telegram_delivery.security import verify_webhook_secret
from app.services.telegram_delivery.service import VisitorBindingWebhookService, WebhookResult

BINDING_QUOTA_NAMESPACE = "sf:demo-telegram:v1:visitor-binding-quota"

QUOTA_SCRIPT = """
local replay = redis.call('GET', KEYS[1])
if replay then return {'REPLAY'} end
local used = tonumber(redis.call('GET', KEYS[2]) or '0')
if used >= tonumber(ARGV[1]) then return {'EXHAUSTED'} end
redis.call('INCR', KEYS[2])
if used == 0 then redis.call('EXPIRE', KEYS[2], ARGV[2]) end
redis.call('SET', KEYS[1], '1', 'EX', ARGV[2])
return {'CLAIMED'}
"""


class BindingQuota(Protocol):
    async def claim(
        self, *, trusted_example_id: str, idempotency_key: str, client_id: str
    ) -> bool: ...


class LegacyUpdateDedupe(Protocol):
    async def claim(self, update_id: int) -> bool: ...


class RedisLegacyUpdateDedupe:
    def __init__(self, redis_url: str, *, ttl_seconds: int = 86400) -> None:
        if not redis_url:
            raise ValueError("telegram_update_dedupe_not_configured")
        self.redis_url, self.ttl_seconds = redis_url, ttl_seconds

    async def claim(self, update_id: int) -> bool:
        digest = hashlib.sha256(str(update_id).encode("ascii")).hexdigest()
        key = f"sf:demo-telegram:v1:update-dedup:legacy:{digest}"
        connection = redis.Redis.from_url(self.redis_url, decode_responses=True)
        try:
            result = await connection.set(key, "1", nx=True, ex=self.ttl_seconds)
        finally:
            await connection.aclose()
        return bool(result)


class RedisBindingQuota:
    """Durable Telegram-only quota; keys contain hashes, never raw visitor data."""

    def __init__(
        self, redis_url: str, *, namespace: str = BINDING_QUOTA_NAMESPACE,
        limit: int = 2, ttl_seconds: int = 3700,
    ) -> None:
        if not redis_url or namespace != BINDING_QUOTA_NAMESPACE or limit != 2:
            raise ValueError("telegram_binding_quota_not_configured")
        self.redis_url = redis_url
        self.namespace = namespace
        self.limit = limit
        self.ttl_seconds = ttl_seconds

    @staticmethod
    def digest(value: str) -> str:
        return hashlib.sha256(value.encode("utf-8")).hexdigest()

    async def claim(
        self, *, trusted_example_id: str, idempotency_key: str, client_id: str
    ) -> bool:
        example = self.digest(trusted_example_id)
        client = self.digest(client_id)
        idem = self.digest(f"{trusted_example_id}:{client_id}:{idempotency_key}")
        quota_key = f"{self.namespace}:quota:{example}:{client}"
        idem_key = f"{self.namespace}:idem:{idem}"
        connection = redis.Redis.from_url(self.redis_url, decode_responses=True)
        try:
            raw = await connection.eval(
                QUOTA_SCRIPT, 2, idem_key, quota_key, str(self.limit), str(self.ttl_seconds)
            )
        finally:
            await connection.aclose()
        return str(raw[0]) in {"CLAIMED", "REPLAY"}


@dataclass(frozen=True, slots=True)
class BindingCreationResult:
    deep_link: DeepLinkResult
    replayed: bool = False


class RuntimeBindingService:
    def __init__(self, deep_links: DeepLinkService, quota: BindingQuota) -> None:
        self.deep_links, self.quota = deep_links, quota

    async def create(
        self, *, origin: str | None, trusted_example_id: str, validated_name: str | None,
        idempotency_key: str, client_id: str,
    ) -> BindingCreationResult:
        trusted = self.deep_links.trusted_origins.get(origin or "")
        if trusted is None or trusted.example_id != trusted_example_id:
            raise PermissionError(BindingState.ORIGIN_MISMATCH.value)
        allowed = await self.quota.claim(
            trusted_example_id=trusted_example_id,
            idempotency_key=idempotency_key,
            client_id=client_id,
        )
        if not allowed:
            raise RuntimeError("telegram_binding_quota_exhausted")
        return BindingCreationResult(
            await self.deep_links.create(origin=origin, validated_name=validated_name)
        )


LegacyHandler = Callable[[dict], Awaitable[None]]


class UnifiedTelegramIngress:
    """Single authenticated ingress; no second provider webhook is required."""

    def __init__(
        self, *, binding_handler: VisitorBindingWebhookService, webhook_secret: str,
        legacy_handler: LegacyHandler | None, legacy_dedupe: LegacyUpdateDedupe | None = None,
    ) -> None:
        if not webhook_secret:
            raise ValueError("telegram_webhook_secret_required")
        self.binding_handler = binding_handler
        self.webhook_secret = webhook_secret
        self.legacy_handler = legacy_handler
        self.legacy_dedupe = legacy_dedupe

    @staticmethod
    def is_binding_update(payload: object) -> bool:
        if not isinstance(payload, dict) or not isinstance(payload.get("message"), dict):
            return False
        text = payload["message"].get("text")
        if not isinstance(text, str):
            return False
        if text.startswith("/start"):
            return True
        return False

    async def handle(
        self, payload: object, supplied_secret: str | None, now_seconds: int
    ) -> WebhookResult:
        if not verify_webhook_secret(self.webhook_secret, supplied_secret):
            raise PermissionError("invalid_telegram_webhook_secret")
        if self.is_binding_update(payload):
            return await self.binding_handler.handle(payload, supplied_secret, now_seconds)
        if self.legacy_handler is None:
            return WebhookResult(BindingState.INVALID_UPDATE)
        if not isinstance(payload, dict):
            return WebhookResult(BindingState.INVALID_UPDATE)
        update_id = payload.get("update_id")
        if not isinstance(update_id, int) or isinstance(update_id, bool):
            return WebhookResult(BindingState.INVALID_UPDATE)
        if self.legacy_dedupe is None or not await self.legacy_dedupe.claim(update_id):
            return WebhookResult(BindingState.REPLAY_BLOCKED)
        await self.legacy_handler(payload)
        return WebhookResult(BindingState.INVALID_UPDATE)
