from __future__ import annotations

from datetime import datetime, timezone

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.journey.models import SiteFormoJourneyProject, SiteFormoVisitor
from app.models.order import Order
from app.schemas.funnel_handoff import FunnelHandoffPayload
from app.services.q1_service import ensure_project


class FunnelHandoffReceiveError(RuntimeError):
    pass


def redeem_from_demo(token: str, receiver_reference: str) -> FunnelHandoffPayload:
    if not settings.demo_handoff_redeem_url or not settings.funnel_handoff_service_secret:
        raise FunnelHandoffReceiveError("Handoff service is unavailable")
    try:
        with httpx.Client(timeout=settings.demo_handoff_timeout_seconds) as client:
            response = client.post(
                settings.demo_handoff_redeem_url,
                headers={"X-SiteFormo-Handoff-Key": settings.funnel_handoff_service_secret},
                json={
                    "handoff_token": token,
                    "receiver": "ie",
                    "receiver_reference": receiver_reference,
                },
            )
        response.raise_for_status()
        payload = FunnelHandoffPayload.model_validate(response.json())
    except (httpx.HTTPError, ValueError):
        raise FunnelHandoffReceiveError("Handoff is unavailable") from None
    if payload.receiver != "ie" or payload.expires_at <= datetime.now(timezone.utc):
        raise FunnelHandoffReceiveError("Handoff is unavailable")
    return payload


def persist_handoff(
    db: Session,
    visitor: SiteFormoVisitor,
    payload: FunnelHandoffPayload,
) -> tuple[Order, bool]:
    order, created = ensure_project(db, visitor, handoff_id=str(payload.handoff_id))
    binding = db.execute(
        select(SiteFormoJourneyProject).where(SiteFormoJourneyProject.order_id == order.id).with_for_update()
    ).scalar_one()
    handoff_id = str(payload.handoff_id)
    if binding.handoff_id not in {None, handoff_id}:
        raise FunnelHandoffReceiveError("Journey already has a different handoff")
    binding.handoff_id = handoff_id
    trace = payload.model_dump(mode="json")
    brief = dict(order.brief_answers or {})
    existing = brief.get("funnel_handoff_v1")
    if existing is not None and existing != trace:
        raise FunnelHandoffReceiveError("Journey already has a different handoff")
    brief["funnel_handoff_v1"] = trace
    order.brief_answers = brief
    if not (order.desired_site_description or "").strip():
        order.desired_site_description = payload.requested_subject
    db.commit()
    db.refresh(order)
    return order, created
