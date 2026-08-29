from __future__ import annotations

import os
import hashlib
import time
from collections.abc import Callable

import httpx
from fastapi import APIRouter, Form, Header, HTTPException, Request, Response, status
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from starlette.responses import JSONResponse

from app.services.delivery.redis_state import RedisDeliveryState
from app.services.contact_delivery.example_scope import ExampleScopeError, resolve_trusted_example
from app.services.sms_delivery.audit import RedisSmsAuditStore
from app.services.sms_delivery.configuration import SmsConfiguration, resolve_sms_configuration
from app.services.sms_delivery.contract import SmsDemoRequest, SmsDemoResponse
from app.services.sms_delivery.service import SmsDeliveryError, SmsDeliveryService
from app.services.sms_delivery.transport import MESSAGE_SID, TwilioSmsTransport
from app.services.voice_delivery.security import validate_twilio_signature

SMS_STATE_NAMESPACE = "sf:demo-sms:v1"

class SmsNoStoreRoute(APIRoute):
    """Keep every SMS endpoint response non-cacheable, including exceptions."""

    def get_route_handler(self):
        original_route_handler = super().get_route_handler()

        async def no_store_route_handler(request: Request):
            try:
                response = await original_route_handler(request)
            except HTTPException as exc:
                headers = dict(exc.headers or {})
                headers["Cache-Control"] = "no-store"
                raise HTTPException(
                    status_code=exc.status_code,
                    detail=exc.detail,
                    headers=headers,
                ) from exc
            except RequestValidationError as exc:
                return JSONResponse(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    content={"detail": jsonable_encoder(exc.errors())},
                    headers={"Cache-Control": "no-store"},
                )
            response.headers["Cache-Control"] = "no-store"
            return response

        return no_store_route_handler


router = APIRouter(tags=["demo-sms"], route_class=SmsNoStoreRoute)
_sms_service: SmsDeliveryService | None = None
_sms_http_client: httpx.AsyncClient | None = None
_sms_audit: RedisSmsAuditStore | None = None
_sms_configuration: SmsConfiguration | None = None


def sms_enabled(environment: dict[str, str] | None = None) -> bool:
    values = environment if environment is not None else os.environ
    return values.get("SMS_DEMO_ENABLED", "false").strip().lower() in {"1", "true", "yes", "on"}


def configure_sms_runtime(
    environment: dict[str, str] | None = None,
    client_factory: Callable[..., httpx.AsyncClient] = httpx.AsyncClient,
) -> bool:
    """Fail closed before constructing Redis, transport or HTTP client."""
    global _sms_service, _sms_http_client, _sms_audit, _sms_configuration
    values = environment if environment is not None else dict(os.environ)
    _sms_service = None
    _sms_audit = None
    _sms_configuration = None
    if not sms_enabled(values):
        return False
    config = resolve_sms_configuration(values)
    try:
        config.require_ready()
    except ValueError:
        return False
    redis_url = values.get("REDIS_URL", "").strip()
    if not redis_url:
        return False
    client = client_factory(timeout=10.0)
    transport = TwilioSmsTransport(
        account_sid=config.account_sid or "",
        auth_token=config.auth_token or "",
        sender_e164=config.sender_e164 or "",
        status_callback_url=f"{config.public_base_url}/api/demo/sms/status",
        client=client,
    )
    _sms_http_client = client
    audit = RedisSmsAuditStore(redis_url)
    _sms_service = SmsDeliveryService(
        config=config,
        state=RedisDeliveryState(redis_url, SMS_STATE_NAMESPACE, limit=2),
        audit=audit,
        transport=transport,
    )
    _sms_audit = audit
    _sms_configuration = config
    return True


async def close_sms_runtime() -> None:
    global _sms_http_client, _sms_service, _sms_audit, _sms_configuration
    if _sms_http_client is not None:
        await _sms_http_client.aclose()
    _sms_http_client = None
    _sms_service = None
    _sms_audit = None
    _sms_configuration = None


SMS_PROVIDER_STATUSES = frozenset({
    "accepted", "queued", "sending", "sent", "delivered", "failed", "undelivered",
})


@router.post("/api/demo/sms/status", response_class=Response)
async def sms_status_callback(
    request: Request,
    MessageSid: str = Form(...),
    MessageStatus: str = Form(...),
    ErrorCode: str = Form(default=""),
    ErrorMessage: str = Form(default=""),
    signature: str | None = Header(default=None, alias="X-Twilio-Signature"),
) -> Response:
    config = _sms_configuration
    if config is None or _sms_audit is None:
        raise HTTPException(status_code=503, detail="sms_runtime_unavailable")
    form = {key: str(value) for key, value in (await request.form()).multi_items()}
    callback_url = f"{config.public_base_url}/api/demo/sms/status"
    if not validate_twilio_signature(callback_url, form, signature, config.auth_token or ""):
        raise HTTPException(status_code=403, detail="invalid_twilio_signature")
    provider_status = MessageStatus.strip().lower()
    if not MESSAGE_SID.fullmatch(MessageSid) or provider_status not in SMS_PROVIDER_STATUSES:
        raise HTTPException(status_code=422, detail="invalid_sms_callback")
    correlated = await _sms_audit.apply_provider_status(
        hashlib.sha256(MessageSid.encode()).hexdigest(),
        provider_status,
        ErrorCode.strip(),
        ErrorMessage.strip(),
        int(time.time()),
    )
    if not correlated:
        raise HTTPException(status_code=409, detail="sms_callback_correlation_pending")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/api/demo/sms/start",
    response_model=SmsDemoResponse,
    status_code=status.HTTP_201_CREATED,
)
async def start_sms_demo(
    payload: SmsDemoRequest,
    request: Request,
    origin: str | None = Header(default=None),
) -> SmsDemoResponse:
    try:
        example_id = resolve_trusted_example(origin, payload.example_id).example_id
    except ExampleScopeError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    if not sms_enabled():
        raise HTTPException(status_code=503, detail="sms_demo_disabled")
    if _sms_service is None:
        raise HTTPException(status_code=503, detail="sms_runtime_unavailable")
    try:
        result = await _sms_service.send(
            example_id=example_id,
            phone=payload.phone,
            message=payload.customer_message,
            first_name=payload.first_name,
            idempotency_key=payload.idempotency_key,
            client_id=request.client.host if request.client else "unknown",
        )
    except SmsDeliveryError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.code) from exc
    return SmsDemoResponse(
        status=result.outcome.value.lower(),
        delivery_reference=result.delivery_reference,
        replayed=result.replayed,
    )
