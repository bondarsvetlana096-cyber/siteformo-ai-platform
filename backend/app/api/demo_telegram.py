from __future__ import annotations

import re
import time
import os

import httpx
from fastapi import APIRouter, Header, HTTPException, Request, Response, status
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.services.telegram_delivery.binding import DeepLinkService, RedisTelegramBindingStore, TrustedExample
from app.services.telegram_delivery.audit import RedisTelegramDeliveryAuditStore
from app.services.telegram_delivery.configuration import Readiness, resolve_configuration
from app.services.telegram_delivery.runtime import (
    RedisBindingQuota, RuntimeBindingService, UnifiedTelegramIngress,
)
from app.services.telegram_delivery.service import VisitorBindingWebhookService
from app.services.telegram_delivery.transport import BotApiTelegramTransport, TelegramTransportConfig

IDEMPOTENCY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{15,127}$")
TRUSTED_EXAMPLE_BY_ORIGIN = {
    "https://dev.siteformo.com": "SF_BU_01_CANONICAL_CONSULTING_EXAMPLE_V1",
    "https://business1.siteformo.com": "SF_BU_01_CANONICAL_CONSULTING_EXAMPLE_V1",
}

router = APIRouter(tags=["demo-telegram"])
_binding_runtime: RuntimeBindingService | None = None
_unified_ingress: UnifiedTelegramIngress | None = None
_telegram_http_client: httpx.AsyncClient | None = None


def configure_telegram_runtime(environment: dict[str, str] | None = None) -> bool:
    """Configure once from server-owned environment; incomplete config stays fail-closed."""
    global _binding_runtime, _unified_ingress, _telegram_http_client
    values = environment if environment is not None else dict(os.environ)
    result = resolve_configuration(values)
    redis_url = values.get("REDIS_URL", "").strip()
    if result.readiness is not Readiness.READY or result.configuration is None or not redis_url:
        _binding_runtime = None
        _unified_ingress = None
        return False
    config = result.configuration
    store = RedisTelegramBindingStore(redis_url, config.binding_namespace)
    trusted = {
        origin: TrustedExample(example_id=example, exact_origin=origin)
        for origin, example in TRUSTED_EXAMPLE_BY_ORIGIN.items()
    }
    deep_links = DeepLinkService(
        store=store, bot_username=config.bot_username,
        ttl_seconds=config.binding_ttl_seconds, trusted_origins=trusted,
    )
    _telegram_http_client = httpx.AsyncClient(timeout=10)
    transport = BotApiTelegramTransport(
        TelegramTransportConfig(config.bot_token), _telegram_http_client
    )
    binding_handler = VisitorBindingWebhookService(
        store=store, audit=RedisTelegramDeliveryAuditStore(redis_url),
        transport=transport, webhook_secret=config.webhook_secret
    )
    _binding_runtime = RuntimeBindingService(deep_links, RedisBindingQuota(redis_url))
    _unified_ingress = UnifiedTelegramIngress(
        binding_handler=binding_handler, webhook_secret=config.webhook_secret,
    )
    return True


async def close_telegram_runtime() -> None:
    global _telegram_http_client
    if _telegram_http_client is not None:
        await _telegram_http_client.aclose()
        _telegram_http_client = None


class TelegramBindingRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    name: str | None = Field(default=None, max_length=100)
    message: str = Field(min_length=1, max_length=240)
    idempotency_key: str = Field(min_length=16, max_length=128)

    @field_validator("name", "message", "idempotency_key")
    @classmethod
    def reject_controls(cls, value: str | None) -> str | None:
        if value is not None and ("\r" in value or "\n" in value or any(ord(c) < 32 or ord(c) == 127 for c in value)):
            raise ValueError("unsafe_control_character")
        return value

    @field_validator("idempotency_key")
    @classmethod
    def validate_idempotency(cls, value: str) -> str:
        if not IDEMPOTENCY.fullmatch(value):
            raise ValueError("invalid_idempotency_key")
        return value


class TelegramBindingResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    url: str
    expires_at: int
    binding_id: str


@router.post(
    "/api/demo/telegram/start",
    response_model=TelegramBindingResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_telegram_binding(
    payload: TelegramBindingRequest,
    request: Request,
    response: Response,
    origin: str | None = Header(default=None),
) -> TelegramBindingResponse:
    trusted_example_id = TRUSTED_EXAMPLE_BY_ORIGIN.get(origin or "")
    if trusted_example_id is None:
        raise HTTPException(status_code=403, detail="origin_not_allowed")
    if _binding_runtime is None:
        raise HTTPException(status_code=503, detail="telegram_binding_unavailable")
    try:
        result = await _binding_runtime.create(
            origin=origin,
            trusted_example_id=trusted_example_id,
            validated_name=payload.name,
            validated_message=payload.message,
            idempotency_key=payload.idempotency_key,
            client_id=request.client.host if request.client else "unknown",
        )
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail="origin_not_allowed") from exc
    except RuntimeError as exc:
        code = "telegram_binding_limit_reached" if str(exc) == "telegram_binding_quota_exhausted" else "telegram_binding_unavailable"
        raise HTTPException(status_code=429 if "limit" in code else 503, detail=code) from exc
    response.headers["Cache-Control"] = "no-store"
    link = result.deep_link
    return TelegramBindingResponse(url=link.url, expires_at=link.expires_at, binding_id=link.binding_id)


@router.post("/api/channels/telegram/webhook")
async def telegram_unified_webhook(
    request: Request,
    secret: str | None = Header(default=None, alias="X-Telegram-Bot-Api-Secret-Token"),
) -> dict[str, bool]:
    if _unified_ingress is None:
        raise HTTPException(status_code=503, detail="telegram_webhook_unavailable")
    try:
        payload = await request.json()
        await _unified_ingress.handle(payload, secret, int(time.time()))
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail="invalid_webhook_secret") from exc
    except Exception:
        # Telegram receives a stable 2xx and no provider/internal diagnostic.
        return {"ok": True}
    return {"ok": True}
