from __future__ import annotations

import hashlib
import json
import logging
import os
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

import httpx
import redis.asyncio as redis

from app.services.contact_delivery.template import RenderedEmail

LOGGER = logging.getLogger(__name__)
TRUSTED_EXAMPLE_ID = "SF_BU_01_CANONICAL_CONSULTING_EXAMPLE_V1"
CANARY_STATE_KEY = "sf:contact-email-canary:v1:single-live-submission"
STATE_TTL_SECONDS = 60 * 60 * 24 * 30


class ClaimKind(StrEnum):
    ACQUIRED = "acquired"
    REPLAY_ACCEPTED = "replay_accepted"
    REPLAY_FAILED = "replay_failed"
    IN_PROGRESS = "in_progress"
    CONSUMED = "consumed"


@dataclass(frozen=True)
class Claim:
    kind: ClaimKind
    provider_message_id: str | None = None
    failure_code: str | None = None


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
    return os.getenv("SF_CONTACT_EMAIL_CANARY_ENABLED", "false").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def allowlisted_recipient() -> str | None:
    value = os.getenv("SF_CONTACT_EMAIL_CANARY_RECIPIENT")
    return value.strip().lower() if value and value.strip() else None


def _redis_client() -> redis.Redis[str]:
    redis_url = os.getenv("REDIS_URL")
    if not redis_url:
        raise RuntimeError("canary_state_unavailable")
    return redis.Redis.from_url(redis_url, decode_responses=True)


def request_fingerprint(payload: dict[str, str]) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _decode_state(raw: str) -> dict[str, str]:
    try:
        value = json.loads(raw)
    except (TypeError, json.JSONDecodeError) as exc:
        raise RuntimeError("canary_state_invalid") from exc
    if not isinstance(value, dict):
        raise TypeError("canary_state_invalid")
    return {str(key): str(item) for key, item in value.items() if item is not None}


async def claim_once(idempotency_key: str, fingerprint: str) -> Claim:
    client = _redis_client()
    initial = json.dumps(
        {
            "idempotency_key": idempotency_key,
            "fingerprint": fingerprint,
            "status": "pending",
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    try:
        acquired = await client.set(CANARY_STATE_KEY, initial, nx=True, ex=STATE_TTL_SECONDS)
        if acquired:
            return Claim(ClaimKind.ACQUIRED)
        raw = await client.get(CANARY_STATE_KEY)
    finally:
        await client.close()

    if not raw:
        raise RuntimeError("canary_state_unavailable")
    state = _decode_state(raw)
    if state.get("idempotency_key") != idempotency_key or state.get("fingerprint") != fingerprint:
        return Claim(ClaimKind.CONSUMED)
    status = state.get("status")
    if status == "accepted" and state.get("provider_message_id"):
        return Claim(ClaimKind.REPLAY_ACCEPTED, provider_message_id=state["provider_message_id"])
    if status == "failed":
        return Claim(ClaimKind.REPLAY_FAILED, failure_code=state.get("failure_code", "provider_failed"))
    return Claim(ClaimKind.IN_PROGRESS)


async def finalize_state(
    idempotency_key: str,
    fingerprint: str,
    *,
    status: str,
    provider_message_id: str | None = None,
    failure_code: str | None = None,
) -> None:
    client = _redis_client()
    current = await client.get(CANARY_STATE_KEY)
    if not current:
        await client.close()
        raise RuntimeError("canary_state_unavailable")
    state = _decode_state(current)
    if state.get("idempotency_key") != idempotency_key or state.get("fingerprint") != fingerprint:
        await client.close()
        raise RuntimeError("canary_state_conflict")
    final = {
        "idempotency_key": idempotency_key,
        "fingerprint": fingerprint,
        "status": status,
    }
    if provider_message_id:
        final["provider_message_id"] = provider_message_id
    if failure_code:
        final["failure_code"] = failure_code
    await client.set(CANARY_STATE_KEY, json.dumps(final, sort_keys=True, separators=(",", ":")), ex=STATE_TTL_SECONDS)
    await client.close()


async def send_with_resend(message: RenderedEmail, recipient: str, idempotency_key: str) -> ProviderAcceptance:
    api_key = os.getenv("RESEND_API_KEY")
    if not api_key:
        raise ProviderError("provider_not_configured", 503)
    provider_key = hashlib.sha256(
        f"{TRUSTED_EXAMPLE_ID}:{idempotency_key}".encode()
    ).hexdigest()
    request: dict[str, Any] = {
        "from": message.sender,
        "to": [recipient],
        "reply_to": message.reply_to,
        "subject": message.subject,
        "html": message.html,
        "text": message.text,
        "tags": [
            {"name": "example_id", "value": TRUSTED_EXAMPLE_ID},
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
        "contact_email_canary provider_accepted example=%s operation=%s",
        TRUSTED_EXAMPLE_ID,
        hashlib.sha256(idempotency_key.encode("utf-8")).hexdigest()[:16],
    )
    return ProviderAcceptance(message_id=message_id.strip(), http_status=response.status_code)
