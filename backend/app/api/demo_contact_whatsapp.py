from __future__ import annotations

import os
import re
from collections.abc import Callable

import httpx
from fastapi import APIRouter, Header, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.services.whatsapp_delivery.binding import trusted_example_for_origin
from app.services.delivery.redis_state import RedisDeliveryState
from app.services.whatsapp_delivery.configuration import (
    FlagStatus,
    Readiness,
    resolve_railway_twilio_configuration,
)
from app.services.whatsapp_delivery.models import normalize_e164
from app.services.whatsapp_delivery.readiness import PublicReadinessInput, require_public_readiness
from app.services.whatsapp_delivery.service import DeliveryError, WhatsAppDeliveryService
from app.services.whatsapp_delivery.transport import MessageMode, TwilioConfig, TwilioWhatsAppTransport

IDEMPOTENCY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{15,127}$")
router = APIRouter(prefix="/api/v1/demo-contact", tags=["demo-contact"])
_service_override: WhatsAppDeliveryService | None = None
_whatsapp_service: WhatsAppDeliveryService | None = None
_whatsapp_http_client: httpx.AsyncClient | None = None


class ContactWhatsAppRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    first_name: str | None = Field(default=None, max_length=100)
    phone: str = Field(min_length=3, max_length=16)
    idempotency_key: str = Field(min_length=16, max_length=128)

    @field_validator("first_name", mode="before")
    @classmethod
    def normalize_optional_first_name(cls, value: object) -> object:
        if value is None:
            return None
        if isinstance(value, str):
            normalized = value.strip()
            return normalized or None
        return value

    @field_validator("first_name", "phone", "idempotency_key")
    @classmethod
    def reject_controls(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if "\r" in value or "\n" in value or any(ord(char) < 32 or ord(char) == 127 for char in value):
            raise ValueError("unsafe control character")
        return value

    @model_validator(mode="after")
    def validate_contract(self) -> ContactWhatsAppRequest:
        if not IDEMPOTENCY.fullmatch(self.idempotency_key):
            raise ValueError("invalid idempotency_key")
        self.phone = normalize_e164(self.phone)
        return self


class ContactWhatsAppResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: str
    message_id: str
    replayed: bool = False
    remaining_deliveries: int = Field(ge=0, le=2)


def enabled() -> bool:
    return os.getenv("SF_CONTACT_WHATSAPP_PUBLIC_DEMO_ENABLED", "false").strip().lower() in {
        "1", "true", "yes", "on"
    }


def configure_whatsapp_runtime(
    environment: dict[str, str] | None = None,
    client_factory: Callable[..., httpx.AsyncClient] = httpx.AsyncClient,
) -> bool:
    """Fail closed before constructing Redis state, transport, or an HTTP client."""
    global _whatsapp_service, _whatsapp_http_client
    values = environment if environment is not None else dict(os.environ)
    _whatsapp_service = None
    _whatsapp_http_client = None
    configuration = resolve_railway_twilio_configuration(values)
    if configuration.public_demo_flag is not FlagStatus.ENABLED:
        return False
    redis_url = values.get("REDIS_URL", "").strip()
    if configuration.readiness is not Readiness.READY or not redis_url:
        return False

    twilio = TwilioConfig(
        account_sid=configuration.account_sid.value or "",
        auth_token=configuration.auth_token.value or "",
        message_mode=MessageMode.BUSINESS_INITIATED_TEMPLATE,
        sender_e164=configuration.sender.value,
        messaging_service_sid=configuration.messaging_service_sid.value,
        content_sid=configuration.content_sid.value,
    )
    client = client_factory(timeout=httpx.Timeout(10.0))
    transport = TwilioWhatsAppTransport(twilio, client)
    state = RedisDeliveryState(redis_url, "sf:demo-whatsapp:v1", limit=2)

    def readiness_check() -> None:
        require_public_readiness(
            PublicReadinessInput(
                configuration=configuration,
                origin_allowed=True,
                redis_available=True,
                circuit_open=False,
            )
        )

    _whatsapp_http_client = client
    _whatsapp_service = WhatsAppDeliveryService(state, transport, readiness_check)
    return True


async def close_whatsapp_runtime() -> None:
    global _whatsapp_http_client, _whatsapp_service
    if _whatsapp_http_client is not None:
        await _whatsapp_http_client.aclose()
    _whatsapp_http_client = None
    _whatsapp_service = None


@router.post("/whatsapp", response_model=ContactWhatsAppResponse, status_code=status.HTTP_202_ACCEPTED)
async def send_demo_contact_whatsapp(
    payload: ContactWhatsAppRequest,
    request: Request,
    origin: str | None = Header(default=None),
    content_length: int | None = Header(default=None),
) -> ContactWhatsAppResponse:
    example = trusted_example_for_origin(origin)
    if not example:
        raise HTTPException(status_code=403, detail="origin_not_allowed")
    if content_length is not None and content_length > 16_384:
        raise HTTPException(status_code=413, detail="request_too_large")
    if not enabled():
        raise HTTPException(
            status_code=503,
            detail="live_whatsapp_disabled",
            headers={"Cache-Control": "no-store"},
        )
    service = _service_override or _whatsapp_service
    if service is None:
        raise HTTPException(
            status_code=503,
            detail="provider_not_configured",
            headers={"Cache-Control": "no-store"},
        )
    try:
        result = await service.send(
            example_id=example.example_id,
            phone=payload.phone,
            first_name=payload.first_name,
            idempotency_key=payload.idempotency_key,
            client_id=request.client.host if request.client else "unknown",
        )
    except DeliveryError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.code) from exc
    return ContactWhatsAppResponse(
        status=result[0], message_id=result[1], replayed=result[2], remaining_deliveries=result[3]
    )
