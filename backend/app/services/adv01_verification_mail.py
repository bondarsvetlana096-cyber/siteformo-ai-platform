from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import re
import time
from dataclasses import dataclass
from html import escape
from typing import Any, Protocol

import httpx

LOGGER = logging.getLogger("siteformo.adv01_verification_mail")
PURPOSE = "adv01_email_verification"
ENDPOINT_PATH = "/api/v1/adv01/verification-email"
VERIFICATION_PATH = "/verify-email/"
REQUEST_TTL_SECONDS = 86_400
SIGNATURE_WINDOW_SECONDS = 300
MAX_BODY_BYTES = 4_096
UUID4 = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$",
    re.IGNORECASE,
)


class BridgeError(Exception):
    def __init__(self, code: str, status_code: int):
        super().__init__(code)
        self.code = code
        self.status_code = status_code


class ProviderRejected(BridgeError):
    def __init__(self) -> None:
        super().__init__("provider_rejected", 502)


class ProviderResultUnknown(BridgeError):
    def __init__(self) -> None:
        super().__init__("provider_result_unknown", 504)


@dataclass(frozen=True)
class BridgeConfig:
    enabled: bool
    secret: str
    redis_url: str
    allowed_host: str
    sender: str
    reply_to: str

    @classmethod
    def from_env(cls) -> "BridgeConfig":
        return cls(
            enabled=os.getenv("ADV01_MAIL_BRIDGE_ENABLED", "false").strip().lower()
            in {"1", "true", "yes", "on"},
            secret=os.getenv("ADV01_MAIL_BRIDGE_HMAC_SECRET", ""),
            redis_url=os.getenv("REDIS_URL", ""),
            allowed_host=os.getenv(
                "ADV01_VERIFICATION_ALLOWED_HOST", "advanced1.siteformo.com"
            ).strip().lower(),
            sender=os.getenv(
                "ADV01_VERIFICATION_FROM", "SiteFormo <siteformo@siteformo.com>"
            ).strip(),
            reply_to=os.getenv(
                "ADV01_VERIFICATION_REPLY_TO", "siteformo@siteformo.com"
            ).strip(),
        )


@dataclass(frozen=True)
class Claim:
    kind: str
    record: dict[str, Any] | None = None


class ReplayStore(Protocol):
    async def claim(self, request_id: str, digest: str) -> Claim: ...
    async def finalize(self, request_id: str, record: dict[str, Any]) -> None: ...
    async def increment(self, key: str, ttl_seconds: int) -> int: ...


class RedisReplayStore:
    def __init__(self, redis_url: str):
        if not redis_url:
            raise BridgeError("delivery_state_unavailable", 503)
        try:
            import redis.asyncio as redis
        except ImportError as exc:  # pragma: no cover - deployment dependency guard
            raise BridgeError("delivery_state_unavailable", 503) from exc
        self.redis = redis.Redis.from_url(redis_url, decode_responses=True)

    @staticmethod
    def _key(request_id: str) -> str:
        return f"sf:adv01-mail:v1:request:{request_id}"

    async def claim(self, request_id: str, digest: str) -> Claim:
        key = self._key(request_id)
        pending = json.dumps({"digest": digest, "state": "pending"}, separators=(",", ":"))
        try:
            if await self.redis.set(key, pending, ex=REQUEST_TTL_SECONDS, nx=True):
                return Claim("new")
            raw = await self.redis.get(key)
        except Exception as exc:
            raise BridgeError("delivery_state_unavailable", 503) from exc
        if not raw:
            raise BridgeError("delivery_state_unavailable", 503)
        try:
            existing = json.loads(raw)
        except (TypeError, ValueError) as exc:
            raise BridgeError("delivery_state_unavailable", 503) from exc
        if not hmac.compare_digest(str(existing.get("digest", "")), digest):
            return Claim("conflict", existing)
        return Claim(str(existing.get("state", "pending")), existing)

    async def finalize(self, request_id: str, record: dict[str, Any]) -> None:
        try:
            await self.redis.set(
                self._key(request_id),
                json.dumps(record, separators=(",", ":")),
                ex=REQUEST_TTL_SECONDS,
            )
        except Exception as exc:
            raise BridgeError("delivery_state_unavailable", 503) from exc

    async def increment(self, key: str, ttl_seconds: int) -> int:
        try:
            value = await self.redis.incr(key)
            if value == 1:
                await self.redis.expire(key, ttl_seconds)
            return int(value)
        except Exception as exc:
            raise BridgeError("delivery_state_unavailable", 503) from exc


def body_digest(raw_body: bytes) -> str:
    return hashlib.sha256(raw_body).hexdigest()


def canonical_signature_input(timestamp: int, request_id: str, raw_body: bytes) -> bytes:
    return (
        f"v1\nPOST\n{ENDPOINT_PATH}\n{timestamp}\n{request_id}\n{body_digest(raw_body)}"
    ).encode("utf-8")


def expected_signature(secret: str, timestamp: int, request_id: str, raw_body: bytes) -> str:
    digest = hmac.new(
        secret.encode("utf-8"),
        canonical_signature_input(timestamp, request_id, raw_body),
        hashlib.sha256,
    ).digest()
    return base64.b64encode(digest).decode("ascii")


def authenticate(
    *,
    config: BridgeConfig,
    timestamp_text: str | None,
    request_id: str | None,
    signature: str | None,
    raw_body: bytes,
    now: int | None = None,
) -> None:
    if not config.enabled:
        raise BridgeError("bridge_disabled", 503)
    if not config.secret:
        raise BridgeError("bridge_not_configured", 503)
    if not request_id or not UUID4.fullmatch(request_id):
        raise BridgeError("invalid_request_id", 401)
    try:
        timestamp = int(timestamp_text or "")
    except ValueError as exc:
        raise BridgeError("invalid_timestamp", 401) from exc
    current = int(time.time()) if now is None else now
    if abs(current - timestamp) > SIGNATURE_WINDOW_SECONDS:
        raise BridgeError("stale_timestamp", 401)
    wanted = expected_signature(config.secret, timestamp, request_id, raw_body)
    if not hmac.compare_digest(wanted, signature or ""):
        raise BridgeError("invalid_signature", 401)


def recipient_fingerprint(secret: str, recipient: str) -> str:
    return hmac.new(secret.encode(), recipient.encode(), hashlib.sha256).hexdigest()[:20]


async def enforce_rate_limits(store: ReplayStore, *, secret: str, uid: int, recipient: str) -> None:
    now = int(time.time())
    fingerprint = recipient_fingerprint(secret, recipient)
    checks = (
        (f"sf:adv01-mail:v1:rate:uid:{uid}:{now // 3600}", 3_600, 3),
        (f"sf:adv01-mail:v1:rate:recipient-hour:{fingerprint}:{now // 3600}", 3_600, 3),
        (f"sf:adv01-mail:v1:rate:recipient-day:{fingerprint}:{now // 86400}", 86_400, 10),
        (f"sf:adv01-mail:v1:rate:global:{now // 60}", 60, 60),
    )
    for key, ttl, limit in checks:
        if await store.increment(key, ttl) > limit:
            raise BridgeError("rate_limited", 429)


def verification_html(verification_url: str) -> str:
    safe_url = escape(verification_url, quote=True)
    return (
        '<div style="font-family:Arial,sans-serif;line-height:1.6;color:#111">'
        "<h2>Confirm your NORTH / KEY account</h2>"
        "<p>Confirm your email address to activate your account.</p>"
        f'<p><a href="{safe_url}">Confirm email</a></p>'
        "<p>If you did not request this account, ignore this email.</p>"
        "</div>"
    )


async def send_verification_email(
    *, recipient: str, verification_url: str, request_id: str, config: BridgeConfig
) -> str:
    api_key = os.getenv("RESEND_API_KEY", "")
    if not api_key:
        raise ProviderRejected()
    payload = {
        "from": config.sender,
        "to": [recipient],
        "reply_to": config.reply_to,
        "subject": "Confirm your NORTH / KEY account",
        "html": verification_html(verification_url),
    }
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(10.0)) as client:
            response = await client.post(
                "https://api.resend.com/emails",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                    "Idempotency-Key": request_id,
                },
                json=payload,
            )
    except httpx.TimeoutException as exc:
        raise ProviderResultUnknown() from exc
    except httpx.HTTPError as exc:
        raise ProviderResultUnknown() from exc
    if response.status_code < 200 or response.status_code >= 300:
        raise ProviderRejected()
    try:
        response_body = response.json()
    except ValueError as exc:
        raise ProviderResultUnknown() from exc
    message_id = response_body.get("id") if isinstance(response_body, dict) else None
    if not isinstance(message_id, str) or not message_id.strip() or len(message_id) > 128:
        raise ProviderResultUnknown()
    return message_id.strip()


def safe_log(
    level: int,
    event: str,
    *,
    request_id: str,
    uid: int | None = None,
    recipient_hash: str | None = None,
    outcome: str | None = None,
) -> None:
    LOGGER.log(
        level,
        "adv01_verification_mail event=%s request_id=%s uid=%s recipient_hash=%s outcome=%s",
        event,
        request_id,
        uid if uid is not None else "",
        recipient_hash or "",
        outcome or "",
    )
