from __future__ import annotations

import os
from collections.abc import Callable

import httpx
from fastapi import APIRouter, Header, HTTPException, Request, Response, status

from app.services.delivery.redis_state import RedisDeliveryState
from app.services.sms_delivery.audit import RedisSmsAuditStore
from app.services.sms_delivery.configuration import resolve_sms_configuration
from app.services.sms_delivery.contract import SmsDemoRequest, SmsDemoResponse
from app.services.sms_delivery.service import SmsDeliveryError, SmsDeliveryService
from app.services.sms_delivery.transport import TwilioSmsTransport

TRUSTED_SMS_EXAMPLE_BY_ORIGIN = {
    "https://dev.siteformo.com": "SF_BU_01_CANONICAL_CONSULTING_EXAMPLE_V1",
}
SMS_STATE_NAMESPACE = "sf:demo-sms:v1"

router = APIRouter(tags=["demo-sms"])
_sms_service: SmsDeliveryService | None = None
_sms_http_client: httpx.AsyncClient | None = None


def sms_enabled(environment: dict[str, str] | None = None) -> bool:
    values = environment if environment is not None else os.environ
    return values.get("SMS_DEMO_ENABLED", "false").strip().lower() in {"1", "true", "yes", "on"}


def configure_sms_runtime(
    environment: dict[str, str] | None = None,
    client_factory: Callable[..., httpx.AsyncClient] = httpx.AsyncClient,
) -> bool:
    """Fail closed before constructing Redis, transport or HTTP client."""
    global _sms_service, _sms_http_client
    values = environment if environment is not None else dict(os.environ)
    _sms_service = None
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
        client=client,
    )
    _sms_http_client = client
    _sms_service = SmsDeliveryService(
        config=config,
        state=RedisDeliveryState(redis_url, SMS_STATE_NAMESPACE, limit=1),
        audit=RedisSmsAuditStore(redis_url),
        transport=transport,
    )
    return True


async def close_sms_runtime() -> None:
    global _sms_http_client, _sms_service
    if _sms_http_client is not None:
        await _sms_http_client.aclose()
    _sms_http_client = None
    _sms_service = None


@router.post(
    "/api/demo/sms/start",
    response_model=SmsDemoResponse,
    status_code=status.HTTP_201_CREATED,
)
async def start_sms_demo(
    payload: SmsDemoRequest,
    request: Request,
    response: Response,
    origin: str | None = Header(default=None),
) -> SmsDemoResponse:
    example_id = TRUSTED_SMS_EXAMPLE_BY_ORIGIN.get(origin or "")
    if example_id is None:
        raise HTTPException(status_code=403, detail="origin_not_allowed")
    response.headers["Cache-Control"] = "no-store"
    if not sms_enabled():
        raise HTTPException(status_code=503, detail="sms_demo_disabled")
    if _sms_service is None:
        raise HTTPException(status_code=503, detail="sms_runtime_unavailable")
    try:
        result = await _sms_service.send(
            example_id=example_id,
            phone=payload.phone,
            message=payload.message,
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
