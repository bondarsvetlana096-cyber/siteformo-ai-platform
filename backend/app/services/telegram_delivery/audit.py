from __future__ import annotations

from enum import StrEnum
from typing import Protocol

import redis.asyncio as redis

from app.services.telegram_delivery.models import AUDIT_NAMESPACE, BindingState

AUDIT_TTL_SECONDS = 7 * 24 * 60 * 60


class AuditOutcome(StrEnum):
    ACCEPTED = "ACCEPTED"
    AMBIGUOUS = "AMBIGUOUS"
    REJECTED = "REJECTED"
    QUARANTINED = "QUARANTINED"


class DeliveryAuditStore(Protocol):
    async def create(self, **values: object) -> None: ...
    async def mark_transport_invoked(self, *, binding_id: str, now_seconds: int) -> None: ...
    async def finalize(self, **values: object) -> None: ...


class RedisTelegramDeliveryAuditStore:
    """Privacy-safe durable evidence for visitor demo deliveries only."""

    def __init__(
        self, redis_url: str, namespace: str = AUDIT_NAMESPACE,
        ttl_seconds: int = AUDIT_TTL_SECONDS,
    ) -> None:
        if not redis_url or namespace != AUDIT_NAMESPACE or ttl_seconds < 86400:
            raise ValueError("telegram_delivery_audit_not_configured")
        self.redis_url, self.namespace, self.ttl_seconds = redis_url, namespace, ttl_seconds

    def client(self) -> redis.Redis[str]:
        return redis.Redis.from_url(self.redis_url, decode_responses=True)

    def key(self, binding_id: str) -> str:
        return f"{self.namespace}:binding:{binding_id}"

    async def create(self, **values: object) -> None:
        now = int(values["now_seconds"])
        key = self.key(str(values["binding_id"]))
        client = self.client()
        try:
            created = await client.hsetnx(key, "binding_id", values["binding_id"])
            if not created:
                raise RuntimeError("telegram_delivery_audit_collision")
            await client.hset(key, mapping={
                "token_hash": values["token_hash"],
                "update_id_hash": values["update_id_hash"],
                "target_chat_id_hash": values["target_chat_id_hash"],
                "transport_invoked": "false",
                "provider_call_count": "0",
                "http_status": "",
                "provider_ok": "",
                "message_id_present": "false",
                "message_id_hash": "",
                "typed_transport_outcome": "",
                "final_binding_state": BindingState.CONSUMED.value,
                "created_at": str(now),
                "updated_at": str(now),
                "expires_at": str(now + self.ttl_seconds),
            })
            await client.expire(key, self.ttl_seconds)
        finally:
            await client.aclose()

    async def mark_transport_invoked(self, *, binding_id: str, now_seconds: int) -> None:
        key = self.key(binding_id)
        client = self.client()
        try:
            if not await client.exists(key):
                raise RuntimeError("telegram_delivery_audit_missing")
            count = await client.hincrby(key, "provider_call_count", 1)
            if count != 1:
                raise RuntimeError("telegram_delivery_provider_call_count_invalid")
            await client.hset(key, mapping={"transport_invoked": "true", "updated_at": str(now_seconds)})
        finally:
            await client.aclose()

    async def finalize(self, **values: object) -> None:
        key = self.key(str(values["binding_id"]))
        client = self.client()
        try:
            if not await client.exists(key):
                raise RuntimeError("telegram_delivery_audit_missing")
            provider_ok = values["provider_ok"]
            await client.hset(key, mapping={
                "http_status": "" if values["http_status"] is None else str(values["http_status"]),
                "provider_ok": "" if provider_ok is None else str(bool(provider_ok)).lower(),
                "message_id_present": str(bool(values["message_id_hash"])).lower(),
                "message_id_hash": values["message_id_hash"],
                "typed_transport_outcome": values["outcome"].value,
                "final_binding_state": values["final_state"].value,
                "updated_at": str(values["now_seconds"]),
            })
        finally:
            await client.aclose()
