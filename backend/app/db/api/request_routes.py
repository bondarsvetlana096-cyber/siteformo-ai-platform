from __future__ import annotations

from datetime import datetime, timezone
import uuid
from urllib.parse import quote

from fastapi import APIRouter, Depends, Header, HTTPException, Request as FastAPIRequest
from fastapi.responses import HTMLResponse, Response
from sqlalchemy import select
from sqlalchemy.orm import Session
from itsdangerous import BadSignature, SignatureExpired

from app.core.config import settings
from app.core.security import unsign_asset
from app.db.session import get_db
from app.models.request import ContactType, Request, RequestStatus
from app.schemas.request import CreateRequestPayload, CreateRequestResponse, RequestEventPayload, RequestStatusResponse
from app.services.publisher import build_demo_delivery_html
from app.services.queue import enqueue_job
from app.services.request_service import create_request, record_request_event
from app.services.storage import StorageError, get_storage

router = APIRouter(tags=["demo-requests"])


def _request_pk(request_id: str):
    try:
        return uuid.UUID(str(request_id))
    except Exception:
        return request_id


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _headers_dict(request: FastAPIRequest) -> dict[str, str]:
    return {k.lower(): v for k, v in request.headers.items()}


def _continue_url(request_id: str) -> str:
    base = (settings.main_site_base_url or str(settings.public_base_url)).rstrip("/")
    path = settings.main_site_continue_path or "/continue"
    separator = "&" if "?" in path else "?"
    return f"{base}{path}{separator}request_id={quote(request_id)}"


def _response_for_request(status: str, req: Request | None) -> CreateRequestResponse:
    if req is None:
        return CreateRequestResponse(status="accepted", message="Request accepted")

    confirmation_required = req.contact_type != ContactType.EMAIL and req.contact_confirmed_at is None
    message = (
        "Send the generated message to SiteFormo, then confirm it was sent."
        if confirmation_required
        else "Demo generation started."
    )

    return CreateRequestResponse(
        status="limit_reached" if status == RequestStatus.LIMIT_REACHED else "accepted",
        request_id=str(req.id),
        message=message,
        redirect_url=req.demo_url,
        confirmation_required=confirmation_required,
        confirmation_token=req.contact_confirmation_token if confirmation_required else None,
        confirmation_text=req.outbound_message_text if confirmation_required else None,
        confirmation_link=(req.generation_metadata or {}).get("confirmation_link") if confirmation_required else None,
        channel_contact=(req.generation_metadata or {}).get("channel_contact") if confirmation_required else None,
    )


@router.post("/api/requests", response_model=CreateRequestResponse)
def create_demo_request(payload: CreateRequestPayload, request: FastAPIRequest, db: Session = Depends(get_db)):
    status, req, _ = create_request(db, payload, _headers_dict(request))
    return _response_for_request(status, req)


@router.get("/api/requests/{request_id}", response_model=RequestStatusResponse)
def get_demo_request_status(request_id: str, db: Session = Depends(get_db)):
    req = db.get(Request, _request_pk(request_id))
    if req is None:
        raise HTTPException(status_code=404, detail="Request not found")

    confirmation_required = req.contact_type != ContactType.EMAIL and req.contact_confirmed_at is None
    return RequestStatusResponse(
        request_id=str(req.id),
        status=req.status,
        contact_type=req.contact_type,
        contact_value=req.contact_value,
        demo_url=req.demo_url,
        expires_at=req.expires_at,
        retention_expires_at=req.retention_expires_at,
        error_message=req.error_message,
        confirmation_required=confirmation_required,
        confirmation_text=req.outbound_message_text if confirmation_required else None,
        confirmation_link=(req.generation_metadata or {}).get("confirmation_link") if confirmation_required else None,
        demo_opened_at=req.demo_opened_at,
        demo_cta_clicked_at=req.demo_cta_clicked_at,
        main_form_started_at=req.main_form_started_at,
        main_form_completed_at=req.main_form_completed_at,
        payment_started_at=req.payment_started_at,
        payment_completed_at=req.payment_completed_at,
        follow_up_count=req.follow_up_count,
    )


@router.post("/api/requests/{request_id}/confirm")
def confirm_demo_contact(request_id: str, db: Session = Depends(get_db)):
    req = db.get(Request, _request_pk(request_id))
    if req is None:
        raise HTTPException(status_code=404, detail="Request not found")
    if req.contact_type == ContactType.EMAIL:
        return {"request_id": str(req.id), "status": req.status, "message": "Email requests do not require channel confirmation."}
    if req.contact_confirmed_at is None:
        req.contact_confirmed_at = _now()
    if req.status == RequestStatus.CREATED:
        req.status = RequestStatus.QUEUED
        enqueue_job(db, "generate_demo", {"request_id": str(req.id)})
    db.commit()
    db.refresh(req)
    return {"request_id": str(req.id), "status": req.status, "message": "Contact confirmed. Demo generation started."}


@router.post("/api/requests/{request_id}/events")
def request_event(request_id: str, payload: RequestEventPayload, db: Session = Depends(get_db)):
    req = db.get(Request, _request_pk(request_id))
    if req is None:
        raise HTTPException(status_code=404, detail="Request not found")
    record_request_event(db, req, payload.event_type, payload.metadata)
    return {"ok": True, "event_type": payload.event_type}


@router.get("/demo/{demo_token}", response_class=HTMLResponse)
def view_demo(demo_token: str, db: Session = Depends(get_db)):
    req = db.execute(select(Request).where(Request.demo_token == demo_token)).scalar_one_or_none()
    if req is None:
        raise HTTPException(status_code=404, detail="Demo not found")
    if not req.master_storage_key:
        raise HTTPException(status_code=409, detail="Demo is not ready yet")
    if req.expires_at and req.expires_at <= _now():
        raise HTTPException(status_code=410, detail="This public demo expired after 10 minutes")

    try:
        html = get_storage().read_text(req.master_storage_key)
    except StorageError as exc:
        raise HTTPException(status_code=404, detail="Demo asset not found") from exc

    if req.demo_opened_at is None:
        record_request_event(db, req, "demo_opened", {"source": "demo_view"})

    return HTMLResponse(
        build_demo_delivery_html(
            request_id=str(req.id),
            demo_token=demo_token,
            html=html,
            continue_url=_continue_url(str(req.id)),
        )
    )


@router.get("/demo-assets/{asset_token}")
def read_demo_asset(asset_token: str, range: str | None = Header(default=None)):
    try:
        storage_key = unsign_asset(asset_token, max_age_seconds=settings.demo_retention_hours * 3600)
        data, content_type = get_storage().get_bytes(storage_key)
    except (BadSignature, SignatureExpired):
        raise HTTPException(status_code=403, detail="Invalid or expired asset token")
    except StorageError as exc:
        raise HTTPException(status_code=404, detail="Asset not found") from exc

    headers = {"Cache-Control": "private, max-age=300", "X-Robots-Tag": "noindex, nofollow, noarchive"}
    return Response(content=data, media_type=content_type, headers=headers)
