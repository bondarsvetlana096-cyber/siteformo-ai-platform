from __future__ import annotations

import logging
import re
import traceback
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request as FastAPIRequest
from fastapi.responses import HTMLResponse, Response
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.security import sha256_text, sign_demo_session, unsign_asset, unsign_demo_session
from app.db.session import get_db
from app.middleware.rate_limit import rate_limit_dependency
from app.models.request import Request, RequestStatus
from app.schemas.request import CreateRequestPayload, CreateRequestResponse, RequestEventPayload, RequestStatusResponse
from app.services.analytics import log_event, log_exception
from app.services.publisher import build_demo_delivery_html
from app.services.request_service import confirm_contact_and_queue, create_request, record_request_event
from app.services.storage import StorageError, get_storage
from app.services.turnstile import verify_turnstile

logger = logging.getLogger("siteformo.api")
router = APIRouter()
REQUEST_ID_FROM_KEY_RE = re.compile(r"^(?:demos|masters)/([^/]+)/")


class ConfirmContactPayload(BaseModel):
    inbound_message_text: str | None = None
    external_user_id: str | None = None


def _client_ip(req: FastAPIRequest) -> str:
    forwarded = req.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return req.client.host if req.client else "0.0.0.0"


def _demo_headers() -> dict[str, str]:
    return {
        "cache-control": "no-store, no-cache, must-revalidate, max-age=0",
        "pragma": "no-cache",
        "expires": "0",
        "x-robots-tag": "noindex, nofollow, noarchive, nosnippet, noimageindex",
        "referrer-policy": "same-origin",
        "x-content-type-options": "nosniff",
        "x-frame-options": "DENY",
        "cross-origin-resource-policy": "same-origin",
        "cross-origin-opener-policy": "same-origin",
        "permissions-policy": "camera=(), microphone=(), geolocation=(), payment=(), usb=(), accelerometer=(), gyroscope=()",
        "content-security-policy": "default-src 'self' 'unsafe-inline' data: blob: https:; img-src 'self' data: blob: https:; media-src 'self' data: blob: https:; style-src 'self' 'unsafe-inline' https:; script-src 'self' 'unsafe-inline'; font-src 'self' data: https:; frame-src 'self' https:; frame-ancestors 'none'; base-uri 'self'; form-action 'self'; connect-src 'self' https:;",
    }


def _asset_headers(content_type: str) -> dict[str, str]:
    return {
        "content-type": content_type,
        "cache-control": "no-store, no-cache, must-revalidate, max-age=0",
        "pragma": "no-cache",
        "expires": "0",
        "x-robots-tag": "noindex, nofollow, noarchive, nosnippet, noimageindex",
        "referrer-policy": "same-origin",
        "x-content-type-options": "nosniff",
        "cross-origin-resource-policy": "same-origin",
    }


def _validate_demo_binding(req_row: Request, request: FastAPIRequest, db: Session) -> None:
    if not settings.demo_bind_ip_enabled:
        return

    ip_hash = sha256_text(_client_ip(request))
    meta = req_row.generation_metadata or {}
    bound_ip_hash = meta.get("demo_bound_ip_hash")

    if bound_ip_hash is None:
        meta["demo_bound_ip_hash"] = ip_hash
        req_row.generation_metadata = meta
        db.commit()
        return

    if bound_ip_hash != ip_hash:
        raise HTTPException(status_code=403, detail="Demo access denied")


@router.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}


@router.post("/api/requests", response_model=CreateRequestResponse, dependencies=[Depends(rate_limit_dependency)])
async def create_request_endpoint(
    payload: CreateRequestPayload,
    request: FastAPIRequest,
    db: Session = Depends(get_db),
) -> CreateRequestResponse:
    try:
        passed = await verify_turnstile(payload.turnstile_token, request.client.host if request.client else None)
        if not passed:
            raise HTTPException(status_code=400, detail="Turnstile verification failed")

        status, req, _delivery = create_request(db, payload, dict(request.headers))

        if status == RequestStatus.LIMIT_REACHED:
            return CreateRequestResponse(status="limit_reached", redirect_url=settings.pricing_redirect_url)

        return CreateRequestResponse(
            status="accepted",
            request_id=str(req.id),
            message="Request accepted for processing",
        )
    except HTTPException:
        raise
    except Exception as exc:
        traceback.print_exc()
        logger.error("[API] error creating request: %s", exc, exc_info=True)
        log_exception(exc, {"stage": "api_create_request"})
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/api/contact-confirmations/{confirmation_token}")
def confirm_contact(
    confirmation_token: str,
    payload: ConfirmContactPayload,
    db: Session = Depends(get_db),
) -> dict[str, str]:
    req = confirm_contact_and_queue(
        db,
        confirmation_token,
        inbound_message_text=payload.inbound_message_text,
        external_user_id=payload.external_user_id,
    )
    if req is None:
        raise HTTPException(status_code=404, detail="Confirmation token not found")
    return {"status": req.status, "request_id": str(req.id)}


@router.get("/api/requests/{request_id}", response_model=RequestStatusResponse)
def get_request_status(request_id: str, db: Session = Depends(get_db)) -> RequestStatusResponse:
    req = db.get(Request, request_id)
    if req is None:
        raise HTTPException(status_code=404, detail="Request not found")

    return RequestStatusResponse(
        request_id=str(req.id),
        status=req.status,
        contact_type=req.contact_type,
        contact_value=req.contact_value,
        demo_url=req.demo_url,
        expires_at=req.expires_at,
        retention_expires_at=req.retention_expires_at,
        error_message=req.error_message,
        confirmation_required=False,
        confirmation_text=req.inbound_message_text,
        confirmation_link=None,
        demo_opened_at=req.demo_opened_at,
        demo_cta_clicked_at=req.demo_cta_clicked_at,
        main_form_started_at=req.main_form_started_at,
        main_form_completed_at=req.main_form_completed_at,
        payment_started_at=req.payment_started_at,
        payment_completed_at=req.payment_completed_at,
        follow_up_count=req.follow_up_count,
    )


@router.post("/api/requests/{request_id}/events")
def track_request_event(
    request_id: str,
    payload: RequestEventPayload,
    db: Session = Depends(get_db),
) -> dict[str, str]:
    req = db.get(Request, request_id)
    if req is None:
        raise HTTPException(status_code=404, detail="Request not found")

    record_request_event(db, req, payload.event_type, payload.metadata)
    return {"status": "ok", "request_id": str(req.id), "event_type": payload.event_type}


@router.get("/demo/{token}", response_class=HTMLResponse)
def get_demo(token: str, request: FastAPIRequest, db: Session = Depends(get_db)) -> HTMLResponse:
    req = db.execute(select(Request).where(Request.demo_token == token)).scalar_one_or_none()

    if req is None or not req.master_storage_key:
        raise HTTPException(status_code=404, detail="Demo not found")

    now = datetime.now(timezone.utc)
    expires_at = req.expires_at
    if expires_at is not None and expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)

    if expires_at and expires_at <= now:
        if req.status != RequestStatus.EXPIRED:
            req.status = RequestStatus.EXPIRED
            db.commit()
        raise HTTPException(status_code=410, detail="Demo expired")

    _validate_demo_binding(req, request, db)

    try:
        body, _ = get_storage().get_bytes(req.master_storage_key)
    except StorageError as exc:
        log_exception(
            exc,
            {
                "stage": "demo_get",
                "request_id": str(req.id),
                "storage_key": req.master_storage_key,
            },
        )
        raise HTTPException(status_code=404, detail="Demo content missing") from exc

    html = body.decode("utf-8", errors="replace")
    delivery_html = build_demo_delivery_html(str(req.id), token, html)

    record_request_event(db, req, "demo_opened", {"token": token})

    response = HTMLResponse(delivery_html, headers=_demo_headers())

    if settings.demo_session_cookie_enabled:
        response.set_cookie(
            key=settings.demo_session_cookie_name,
            value=sign_demo_session(str(req.id)),
            max_age=settings.demo_ttl_minutes * 60,
            httponly=True,
            secure=True,
            samesite="strict",
            path="/",
        )

    return response


@router.get("/demo-assets/{asset_token}")
def get_demo_asset(asset_token: str, request: FastAPIRequest, db: Session = Depends(get_db)) -> Response:
    try:
        key = unsign_asset(asset_token, max_age_seconds=settings.demo_ttl_minutes * 60)
        request_id_match = REQUEST_ID_FROM_KEY_RE.match(key)
        if not request_id_match:
            raise HTTPException(status_code=403, detail="Asset access denied")

        request_id = request_id_match.group(1)
        req = db.get(Request, request_id)
        if req is None or not req.demo_token:
            raise HTTPException(status_code=404, detail="Asset not found")

        if settings.demo_session_cookie_enabled:
            cookie = request.cookies.get(settings.demo_session_cookie_name)
            if not cookie:
                raise HTTPException(status_code=403, detail="Asset session missing")

            cookie_request_id = unsign_demo_session(
                cookie,
                max_age_seconds=settings.demo_ttl_minutes * 60,
            )
            if str(cookie_request_id) != str(request_id):
                raise HTTPException(status_code=403, detail="Asset session mismatch")

        if settings.demo_bind_ip_enabled:
            _validate_demo_binding(req, request, db)

        data, mime = get_storage().get_bytes(key)
        return Response(content=data, media_type=mime, headers=_asset_headers(mime))

    except HTTPException:
        raise
    except (StorageError, Exception) as exc:
        traceback.print_exc()
        log_exception(exc, {"stage": "demo_asset"})
        raise HTTPException(status_code=404, detail=str(exc)) from exc