from __future__ import annotations

from typing import Protocol

import redis.asyncio as redis

SMS_AUDIT_NAMESPACE = "sf:demo-sms:v1:audit"


class SmsAuditStore(Protocol):
    async def create(self, delivery_id: str, values: dict[str, str], ttl_seconds: int) -> None: ...
    async def finalize(self, delivery_id: str, values: dict[str, str]) -> None: ...


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
        finally:
            await client.aclose()
