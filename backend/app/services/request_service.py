from __future__ import annotations

import logging
import secrets
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.security import normalize_email, sha256_text
from app.models.request import ContactType, DemoAsset, Request, RequestStatus, RequestType, UserUsage
from app.schemas.request import CreateRequestPayload
from app.services.analytics import log_event, log_exception
from app.services.email_service import send_demo_email
from app.services.followups import (
    FOLLOWUP_REASON_CHECKOUT,
    FOLLOWUP_REASON_DEMO_CTA,
    FOLLOWUP_REASON_DEMO_READY,
    build_followup_message,
    build_outbound_followup_text,
)
from app.services.generation import generate_demo_page
from app.services.publisher import publish_demo
from app.services.queue import enqueue_job
from app.services.scraper import scrape_site
from app.services.storage import get_storage

logger = logging.getLogger("siteformo.request_service")


BYPASS_LIMIT_EMAILS = {
    "klon97048@gmail.com",
}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _parse_request_uuid(request_id: str):
    try:
        return uuid.UUID(str(request_id))
    except Exception:
        return request_id


def _get_existing_master_asset(db: Session, request_id: str) -> DemoAsset | None:
    return db.execute(
        select(DemoAsset)
        .where(
            DemoAsset.request_id == _parse_request_uuid(request_id),
            DemoAsset.asset_type == "master_html",
            DemoAsset.deleted_at.is_(None),
        )
        .order_by(DemoAsset.created_at.desc())
    ).scalars().first()


def _client_ip(headers: dict[str, str], fallback: str = "0.0.0.0") -> str:
    return headers.get("x-forwarded-for", fallback).split(",")[0].strip()


def _normalize_contact(contact_type: str, contact_value: str) -> tuple[str | None, str]:
    value = contact_value.strip()
    if contact_type == ContactType.EMAIL:
        email = normalize_email(value)
        return email, email
    return None, value.lstrip("@").lower()


def _build_user_identity(contact_type: str, normalized_contact: str, ip: str, fingerprint: str | None) -> str:
    raw = f"{contact_type}|{normalized_contact}|{ip}|{fingerprint or ''}"
    return sha256_text(raw)


def _schedule_followup(db: Session, request_id: str, reason: str, delay_minutes: int) -> None:
    scheduled_at = _utcnow() + timedelta(minutes=delay_minutes)
    enqueue_job(db, "follow_up_check", {"request_id": request_id, "reason": reason}, scheduled_at=scheduled_at)


def _is_limit_bypass_email(email: str | None) -> bool:
    if not email:
        return False
    return normalize_email(email) in BYPASS_LIMIT_EMAILS


def create_request(db: Session, payload: CreateRequestPayload, headers: dict[str, str]) -> tuple[str, Request | None, dict | None]:
    ip = _client_ip(headers)
    ip_hash = sha256_text(ip)
    normalized_email, normalized_contact = _normalize_contact(payload.contact_type, payload.contact_value or "")
    identity_hash = _build_user_identity(payload.contact_type, normalized_contact, ip, payload.fingerprint)

    bypass_limits = _is_limit_bypass_email(normalized_email)

    usage = db.execute(select(UserUsage).where(UserUsage.user_identity_hash == identity_hash)).scalar_one_or_none()
    if usage is None:
        usage = UserUsage(
            email_normalized=normalized_contact,
            fingerprint=payload.fingerprint,
            ip_hash=ip_hash,
            user_identity_hash=identity_hash,
            free_attempts_used=0,
        )
        db.add(usage)
        db.commit()
        db.refresh(usage)

    if bypass_limits:
        logger.info("[API] limit bypass applied: email=%s", normalized_email)

    if not bypass_limits and usage.free_attempts_used >= settings.free_attempt_limit:
        req = Request(
            request_type=payload.request_type,
            email=normalized_email,
            contact_type=payload.contact_type,
            contact_value=normalized_contact,
            source_url=str(payload.source_url) if payload.source_url else None,
            business_description=payload.business_description,
            status=RequestStatus.LIMIT_REACHED,
            fingerprint=payload.fingerprint,
            ip_hash=ip_hash,
            user_identity_hash=identity_hash,
            attempt_number=usage.free_attempts_used + 1,
        )
        db.add(req)
        db.commit()
        db.refresh(req)
        logger.warning("[API] limit reached: contact=%s", normalized_contact)
        log_event(
            db,
            "request_limit_reached",
            request_id=req.id,
            payload={"contact_type": payload.contact_type},
            distinct_id=normalized_contact,
        )
        return RequestStatus.LIMIT_REACHED, req, None

    current_attempt_number = 1 if bypass_limits else usage.free_attempts_used + 1

    req = Request(
        request_type=payload.request_type,
        email=normalized_email,
        contact_type=payload.contact_type,
        contact_value=normalized_contact,
        source_url=str(payload.source_url) if payload.source_url else None,
        business_description=payload.business_description,
        status=RequestStatus.QUEUED,
        contact_confirmation_token=secrets.token_urlsafe(12),
        fingerprint=payload.fingerprint,
        ip_hash=ip_hash,
        user_identity_hash=identity_hash,
        attempt_number=current_attempt_number,
    )
    db.add(req)

    if not bypass_limits:
        usage.free_attempts_used += 1

    db.commit()
    db.refresh(req)

    enqueue_job(db, "generate_demo", {"request_id": str(req.id)})
    log_event(
        db,
        "request_queued",
        request_id=req.id,
        payload={"request_type": req.request_type, "contact_type": req.contact_type},
        distinct_id=normalized_contact,
    )
    return RequestStatus.QUEUED, req, None


def confirm_contact_and_queue(
    db: Session,
    confirmation_token: str,
    inbound_message_text: str | None = None,
    external_user_id: str | None = None,
) -> Request | None:
    req = db.execute(select(Request).where(Request.contact_confirmation_token == confirmation_token)).scalar_one_or_none()
    if req is None:
        return None

    req.contact_confirmed_at = _utcnow()
    if inbound_message_text:
        req.inbound_message_text = inbound_message_text
    meta = req.generation_metadata or {}
    if external_user_id:
        meta["external_user_id"] = external_user_id
    req.generation_metadata = meta
    db.commit()
    log_event(db, "contact_confirmed", request_id=req.id, payload={"contact_type": req.contact_type}, distinct_id=req.contact_value)
    return req


async def process_generate_job(db: Session, request_id: str) -> None:
    req = db.get(Request, _parse_request_uuid(request_id))
    if req is None:
        return

    logger.info("[WORKER] start generate: request_id=%s", request_id)

    try:
        req.status = RequestStatus.PROCESSING
        req.error_message = None
        db.commit()
        log_event(db, "generation_started", request_id=req.id, distinct_id=req.contact_value)

        source = None
        if req.request_type == RequestType.REDESIGN and req.source_url:
            source = await scrape_site(req.source_url)

        result = generate_demo_page(req.request_type, source, req.business_description)

        req.status = RequestStatus.GENERATED
        req.generation_metadata = {
            **(req.generation_metadata or {}),
            "title": result["title"],
            "request_type": req.request_type,
            "worker_code_version": "retention_v2",
        }
        db.commit()

        existing_asset = _get_existing_master_asset(db, str(req.id))
        if existing_asset:
            logger.warning(
                "[WORKER] duplicate publish skipped: request_id=%s existing_storage_key=%s",
                request_id,
                existing_asset.storage_key,
            )
            return

        token, master_storage_key, expires_at, retention_expires_at, demo_url = publish_demo(
            str(req.id),
            result["html"],
        )

        logger.info(
            "[STORAGE] publish_demo result: request_id=%s token=%s master_storage_key=%s expires_at=%s retention_expires_at=%s demo_url=%s",
            request_id,
            token,
            master_storage_key,
            expires_at,
            retention_expires_at,
            demo_url,
        )

        if not master_storage_key:
            raise RuntimeError(f"publish_demo returned empty master_storage_key for request_id={request_id}")

        if not retention_expires_at:
            raise RuntimeError(f"publish_demo returned empty retention_expires_at for request_id={request_id}")

        existing_asset = _get_existing_master_asset(db, str(req.id))
        if existing_asset:
            logger.warning(
                "[WORKER] duplicate asset insert skipped: request_id=%s existing_storage_key=%s",
                request_id,
                existing_asset.storage_key,
            )
            return

        req.status = RequestStatus.PUBLISHED
        req.demo_token = token
        req.demo_storage_key = None
        req.master_storage_key = master_storage_key
        req.demo_url = demo_url
        req.expires_at = expires_at
        req.retention_expires_at = retention_expires_at
        req.outbound_message_text = None

        db.add(
            DemoAsset(
                request_id=req.id,
                storage_key=master_storage_key,
                asset_type="master_html",
                expires_at=retention_expires_at,
            )
        )

        db.commit()
        db.refresh(req)

        logger.info(
            "[DB] request saved: request_id=%s master_storage_key=%s retention_expires_at=%s status=%s",
            request_id,
            req.master_storage_key,
            req.retention_expires_at,
            req.status,
        )

        log_event(
            db,
            "demo_published",
            request_id=req.id,
            payload={
                "demo_url": demo_url,
                "master_storage_key": master_storage_key,
                "retention_expires_at": str(retention_expires_at),
            },
            distinct_id=req.contact_value,
        )

        enqueue_job(db, "expire_demo", {"request_id": str(req.id)}, scheduled_at=expires_at)
        enqueue_job(db, "cleanup_demo", {"request_id": str(req.id)}, scheduled_at=retention_expires_at)
        _schedule_followup(db, str(req.id), FOLLOWUP_REASON_DEMO_READY, settings.demo_ready_followup_delay_minutes)

        logger.info("[WORKER] generate complete: request_id=%s", request_id)

    except Exception as exc:
        req.status = RequestStatus.FAILED
        req.error_message = str(exc)
        db.commit()
        log_event(
            db,
            "generation_failed",
            request_id=req.id,
            payload={"error": str(exc)},
            distinct_id=req.contact_value,
        )
        log_exception(exc, {"request_id": request_id, "stage": "generate"})
        raise


def record_request_event(db: Session, req: Request, event_type: str, metadata: dict | None = None) -> Request:
    now = _utcnow()
    if event_type == "demo_opened":
        if req.demo_opened_at is None:
            req.demo_opened_at = now
        _schedule_followup(db, str(req.id), FOLLOWUP_REASON_DEMO_CTA, settings.demo_cta_followup_delay_minutes)
    elif event_type == "demo_cta_clicked":
        if req.demo_cta_clicked_at is None:
            req.demo_cta_clicked_at = now
        _schedule_followup(db, str(req.id), FOLLOWUP_REASON_CHECKOUT, settings.checkout_followup_delay_minutes)
    elif event_type == "main_form_started":
        if req.main_form_started_at is None:
            req.main_form_started_at = now
        _schedule_followup(db, str(req.id), FOLLOWUP_REASON_CHECKOUT, settings.checkout_followup_delay_minutes)
    elif event_type == "main_form_completed":
        req.main_form_completed_at = now
    elif event_type == "payment_started":
        if req.payment_started_at is None:
            req.payment_started_at = now
        _schedule_followup(db, str(req.id), FOLLOWUP_REASON_CHECKOUT, settings.checkout_followup_delay_minutes)
    elif event_type == "payment_completed":
        req.payment_completed_at = now
        req.status = RequestStatus.COMPLETED

    db.commit()
    log_event(db, event_type, request_id=req.id, payload=metadata or {}, distinct_id=req.contact_value)
    return req


async def process_follow_up_job(db: Session, request_id: str, reason: str) -> None:
    req = db.get(Request, _parse_request_uuid(request_id))
    if req is None or not req.demo_url or req.follow_up_count >= settings.max_followup_count:
        return
    if req.status in {RequestStatus.DELETED, RequestStatus.FAILED, RequestStatus.COMPLETED}:
        return

    should_send = False
    if reason == FOLLOWUP_REASON_DEMO_READY:
        should_send = req.demo_opened_at is None
    elif reason == FOLLOWUP_REASON_DEMO_CTA:
        should_send = req.demo_opened_at is not None and req.demo_cta_clicked_at is None
    elif reason == FOLLOWUP_REASON_CHECKOUT:
        should_send = (
            (req.demo_cta_clicked_at is not None or req.main_form_started_at is not None or req.payment_started_at is not None)
            and req.payment_completed_at is None
        )

    if not should_send:
        return

    subject, title, cta_url, body_text = build_followup_message(req, reason)
    if req.contact_type == ContactType.EMAIL and req.email:
        await send_demo_email(
            req.email,
            subject=subject,
            title=title,
            body_text=body_text,
            cta_label="Continue",
            cta_url=cta_url,
            footer_text="All remaining steps are completed on the main Siteformo website.",
        )
    else:
        req.outbound_message_text = build_outbound_followup_text(req, reason)

    req.last_follow_up_sent_at = _utcnow()
    req.last_follow_up_reason = reason
    req.follow_up_count += 1
    db.commit()
    log_event(db, "followup_sent", request_id=req.id, payload={"reason": reason, "cta_url": cta_url}, distinct_id=req.contact_value)


async def process_expire_job(db: Session, request_id: str) -> None:
    req = db.get(Request, _parse_request_uuid(request_id))
    if req is None or req.status in {RequestStatus.EXPIRED, RequestStatus.DELETED}:
        return
    if req.expires_at and req.expires_at > _utcnow():
        return

    logger.info("[WORKER] start expire: request_id=%s", request_id)
    req.status = RequestStatus.EXPIRED
    db.commit()
    log_event(db, "demo_expired", request_id=req.id, payload={"master_storage_key": req.master_storage_key}, distinct_id=req.contact_value)


async def process_cleanup_job(db: Session, request_id: str) -> None:
    req = db.get(Request, _parse_request_uuid(request_id))
    if req is None or req.status == RequestStatus.DELETED:
        return
    if req.retention_expires_at and req.retention_expires_at > _utcnow():
        return

    logger.info("[WORKER] start cleanup: request_id=%s", request_id)
    try:
        if req.master_storage_key:
            get_storage().delete(req.master_storage_key)
        for asset in req.assets:
            try:
                get_storage().delete(asset.storage_key)
            except Exception as asset_exc:
                log_exception(asset_exc, {"request_id": request_id, "stage": "cleanup_asset", "storage_key": asset.storage_key})
            asset.deleted_at = _utcnow()
        req.status = RequestStatus.DELETED
        db.commit()
        log_event(db, "demo_cleaned_up", request_id=req.id, payload={"retention_expires_at": str(req.retention_expires_at)}, distinct_id=req.contact_value)
    except Exception as exc:
        log_exception(exc, {"request_id": request_id, "stage": "cleanup"})
        raise