from __future__ import annotations

import os
from collections.abc import Callable

import httpx
from fastapi import APIRouter, Header, HTTPException, Request, Response, status
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.services.whatsapp_delivery.binding import trusted_example_for_origin
from app.services.whatsapp_delivery.configuration import (
    FlagStatus,
    Readiness,
    resolve_railway_twilio_configuration,
)
from app.services.whatsapp_delivery.customer_initiated import (
    CustomerInitiatedWhatsAppService,
    RedisWhatsAppExampleStore,
    validate_twilio_signature,
)
from app.services.whatsapp_delivery.transport import MessageMode, TwilioConfig, TwilioWhatsAppTransport

router = APIRouter(prefix="/api/v1/demo-contact", tags=["demo-contact"])
_service_override: CustomerInitiatedWhatsAppService | None = None
_whatsapp_service: CustomerInitiatedWhatsAppService | None = None
_whatsapp_http_client: httpx.AsyncClient | None = None
_whatsapp_auth_token: str | None = None
_whatsapp_public_base_url: str | None = None


class ContactWhatsAppRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    first_name: str | None = Field(default=None, max_length=100)

    @field_validator("first_name", mode="before")
    @classmethod
    def normalize_optional_first_name(cls, value: object) -> object:
        if value is None:
            return None
        if isinstance(value, str):
            normalized = value.strip()
            return normalized or None
        return value

    @field_validator("first_name")
    @classmethod
    def reject_controls(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if "\r" in value or "\n" in value or any(ord(char) < 32 or ord(char) == 127 for char in value):
            raise ValueError("unsafe control character")
        return value


class ContactWhatsAppResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: str
    url: str
    correlation_hash: str


def enabled() -> bool:
    return os.getenv("SF_CONTACT_WHATSAPP_PUBLIC_DEMO_ENABLED", "false").strip().lower() in {
        "1", "true", "yes", "on"
    }


def configure_whatsapp_runtime(
    environment: dict[str, str] | None = None,
    client_factory: Callable[..., httpx.AsyncClient] = httpx.AsyncClient,
) -> bool:
    """Fail closed before constructing Redis state, transport, or an HTTP client."""
    global _whatsapp_service, _whatsapp_http_client, _whatsapp_auth_token, _whatsapp_public_base_url
    values = environment if environment is not None else dict(os.environ)
    _whatsapp_service = None
    _whatsapp_http_client = None
    _whatsapp_auth_token = None
    _whatsapp_public_base_url = None
    configuration = resolve_railway_twilio_configuration(values)
    if configuration.public_demo_flag is not FlagStatus.ENABLED:
        return False
    redis_url = values.get("REDIS_URL", "").strip()
    public_base_url = values.get("PUBLIC_BASE_URL", "").strip()
    if (
        configuration.readiness is not Readiness.READY
        or not redis_url
        or not public_base_url.startswith("https://")
        or not configuration.sender.value
    ):
        return False

    twilio = TwilioConfig(
        account_sid=configuration.account_sid.value or "",
        auth_token=configuration.auth_token.value or "",
        message_mode=MessageMode.SESSION_FREEFORM_BODY,
        sender_e164=configuration.sender.value,
    )
    client = client_factory(timeout=httpx.Timeout(10.0))
    transport = TwilioWhatsAppTransport(twilio, client)
    _whatsapp_http_client = client
    _whatsapp_auth_token = configuration.auth_token.value
    _whatsapp_public_base_url = public_base_url.rstrip("/")
    _whatsapp_service = CustomerInitiatedWhatsAppService(
        RedisWhatsAppExampleStore(redis_url), transport, configuration.sender.value, public_base_url
    )
    return True


async def close_whatsapp_runtime() -> None:
    global _whatsapp_http_client, _whatsapp_service, _whatsapp_auth_token, _whatsapp_public_base_url
    if _whatsapp_http_client is not None:
        await _whatsapp_http_client.aclose()
    _whatsapp_http_client = None
    _whatsapp_service = None
    _whatsapp_auth_token = None
    _whatsapp_public_base_url = None


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
        url, correlation_hash = await service.prepare(
            payload.first_name, request.client.host if request.client else "unknown"
        )
    except (RuntimeError, ValueError) as exc:
        raise HTTPException(status_code=503, detail="whatsapp_example_unavailable") from exc
    return ContactWhatsAppResponse(status="ready", url=url, correlation_hash=correlation_hash)


def _empty_twiml(status_code: int = 200) -> Response:
    return Response(
        content='<?xml version="1.0" encoding="UTF-8"?><Response></Response>',
        status_code=status_code,
        media_type="application/xml",
        headers={"Cache-Control": "no-store"},
    )


inbound_router = APIRouter(tags=["whatsapp-inbound"])


@inbound_router.post("/twilio/webhook")
@inbound_router.post("/whatsapp/webhook")
@inbound_router.post("/channels/whatsapp/webhook")
async def receive_customer_initiated_whatsapp(
    request: Request,
    x_twilio_signature: str | None = Header(default=None),
) -> Response:
    form = await request.form()
    params = {str(key): str(value) for key, value in form.multi_items()}
    base_url = _whatsapp_public_base_url or os.getenv("PUBLIC_BASE_URL", "").strip().rstrip("/")
    auth_token = _whatsapp_auth_token or os.getenv("WHATSAPP_TWILIO_AUTH_TOKEN", "").strip()
    external_url = base_url + request.url.path
    if request.url.query:
        external_url += "?" + request.url.query
    if not validate_twilio_signature(external_url, params, x_twilio_signature or "", auth_token):
        return _empty_twiml(403)
    if not enabled():
        return _empty_twiml()
    service = _service_override or _whatsapp_service
    if service is None:
        return _empty_twiml()
    await service.handle_inbound(params)
    return _empty_twiml()
