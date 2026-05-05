from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import text
from sqlalchemy.orm import Session, joinedload

from app.db.session import get_db
from app.models.order import Order, OrderStatus

router = APIRouter(tags=["design-selection"])


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _has_status(name: str) -> bool:
    return hasattr(OrderStatus, name)


def _status(name: str, fallback: str | None = None):
    value = getattr(OrderStatus, name, None)
    if value is not None:
        return value
    if fallback:
        value = getattr(OrderStatus, fallback, None)
        if value is not None:
            return value
    raise HTTPException(status_code=500, detail=f"OrderStatus.{name} is not defined")


def _set_if_exists(obj: Any, field: str, value: Any) -> None:
    if hasattr(obj, field):
        setattr(obj, field, value)


def _get_order_or_404(db: Session, order_id: str) -> Order:
    order = (
        db.query(Order)
        .options(joinedload(Order.client), joinedload(Order.concepts))
        .filter(Order.id == order_id)
        .first()
    )
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    return order


def _concept_previews(order: Order) -> list[dict[str, Any]]:
    concepts = list(getattr(order, "concepts", []) or [])
    result: list[dict[str, Any]] = []
    for concept in sorted(concepts, key=lambda item: getattr(item, "concept_label", "")):
        label = getattr(concept, "concept_label", None) or f"Design {len(result)+1}"
        result.append({
            "id": label,
            "label": label,
            "art_direction": getattr(concept, "art_direction", None),
            "summary": getattr(concept, "summary", None),
            "html": getattr(concept, "html_code", None),
            "image_url": getattr(concept, "preview_image_url", None),
            "screenshot_url": getattr(concept, "preview_image_url", None),
        })
    return result


def _stored_previews(order: Order) -> list[dict[str, Any]]:
    previews = getattr(order, "design_previews", None) or []
    if isinstance(previews, list) and previews:
        return previews
    return _concept_previews(order)


def _find_preview(order: Order, payload: dict[str, Any]) -> dict[str, Any]:
    previews = _stored_previews(order)
    selected_id = payload.get("preview_id") or payload.get("selected_design_id") or payload.get("selected_id") or payload.get("design_id")
    selected_index = payload.get("selected_index")
    selected_url = payload.get("selected_screenshot_url") or payload.get("selected_design_url") or payload.get("preview_url") or payload.get("image_url")

    if selected_id:
        for item in previews:
            if str(item.get("id")) == str(selected_id) or str(item.get("label")) == str(selected_id):
                return item

    if selected_index is not None:
        try:
            index = int(selected_index)
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail="selected_index must be a number") from exc
        if 0 <= index < len(previews):
            return previews[index]
        if 1 <= index <= len(previews):
            return previews[index - 1]

    if selected_url:
        for item in previews:
            if selected_url in {item.get("screenshot_url"), item.get("preview_url"), item.get("image_url")}:
                return item

    if previews:
        raise HTTPException(status_code=400, detail="Select a valid design preview.")

    return {
        "id": selected_id or "design_1",
        "label": payload.get("selected_concept_label") or payload.get("concept_label") or "Design 1",
        "screenshot_url": selected_url,
        "image_url": selected_url,
    }


def _queue_final_generation(db: Session, order_id: str) -> None:
    existing_job = db.execute(
        text(
            """
            select id
            from generation_jobs
            where order_id = :order_id
              and job_type = 'FINAL_GENERATION'
              and status in ('PENDING', 'PROCESSING', 'COMPLETED')
            limit 1
            """
        ),
        {"order_id": str(order_id)},
    ).first()

    if not existing_job:
        db.execute(
            text(
                """
                insert into generation_jobs (order_id, job_type, status)
                values (:order_id, 'FINAL_GENERATION', 'PENDING')
                """
            ),
            {"order_id": str(order_id)},
        )


@router.get("/api/orders/{order_id}/previews")
def get_order_previews(order_id: str, db: Session = Depends(get_db)):
    order = _get_order_or_404(db, order_id)
    return {
        "success": True,
        "order_id": order.id,
        "status": order.status,
        "previews": _stored_previews(order),
        "logo_previews": getattr(order, "logo_previews", None) or [],
        "already_selected": bool(getattr(order, "selected_design_id", None) or getattr(order, "selected_design_label", None)),
        "selected_design_id": getattr(order, "selected_design_id", None),
        "selected_design_label": getattr(order, "selected_design_label", None),
    }


@router.post("/api/orders/{order_id}/approve-design")
def approve_design_compat(order_id: str, payload: dict[str, Any], db: Session = Depends(get_db)):
    # Compatibility endpoint. The same path may also exist in order_routes.py;
    # this version uses the same safe SQLAlchemy flow and queues final generation.
    order = _get_order_or_404(db, order_id)

    blocked = [
        getattr(OrderStatus, name)
        for name in [
            "DESIGN_APPROVED",
            "REFUND_WINDOW_ACTIVE",
            "FULL_PRODUCTION_STARTED",
            "READY_FOR_REVIEW",
            "FINAL_PAYMENT_REQUIRED",
            "DELIVERED",
            "FINAL_READY",
        ]
        if _has_status(name)
    ]

    already_selected = bool(
        getattr(order, "selected_design_id", None)
        or getattr(order, "selected_design_label", None)
        or getattr(order, "selected_screenshot_url", None)
        or getattr(order, "selected_design_url", None)
        or getattr(order, "design_approved_at", None)
    )

    if order.status in blocked or already_selected:
        return {
            "success": False,
            "already_selected": True,
            "order_id": order.id,
            "status": order.status,
            "message": "A design has already been selected for this order.",
        }

    allowed = [getattr(OrderStatus, name) for name in ["DESIGN_PREVIEWS_READY", "AWAITING_CLIENT_DESIGN_CHOICE"] if _has_status(name)]
    if allowed and order.status not in allowed:
        return {
            "success": False,
            "already_selected": False,
            "order_id": order.id,
            "status": order.status,
            "message": f"Design selection is not available for status: {order.status}",
        }

    selected = _find_preview(order, payload)
    selected_id = selected.get("id") or selected.get("label") or "design_1"
    selected_label = selected.get("label") or selected_id
    selected_url = selected.get("screenshot_url") or selected.get("preview_url") or selected.get("image_url")

    now = _now()
    refund_until = now + timedelta(hours=1)

    _set_if_exists(order, "selected_design_id", selected_id)
    _set_if_exists(order, "selected_design_label", selected_label)
    _set_if_exists(order, "selected_design_url", selected_url)
    _set_if_exists(order, "selected_screenshot_url", selected_url)
    _set_if_exists(order, "design_approved_at", now)
    _set_if_exists(order, "refund_window_started_at", now)
    _set_if_exists(order, "refund_window_ends_at", refund_until)
    _set_if_exists(order, "refund_window_expires_at", refund_until)
    _set_if_exists(order, "design_status", "DESIGN_APPROVED")
    _set_if_exists(order, "generation_status", "FULL_PRODUCTION_QUEUED")
    _set_if_exists(order, "full_generation_started_at", now)

    order.status = _status("FULL_PRODUCTION_STARTED", "FINAL_READY")
    _queue_final_generation(db, str(order.id))

    db.commit()
    db.refresh(order)

    return {
        "success": True,
        "already_selected": False,
        "order_id": order.id,
        "status": order.status,
        "selected_design_id": selected_id,
        "selected_design_label": selected_label,
        "selected_screenshot_url": selected_url,
        "refund_window_ends_at": refund_until.isoformat(),
        "message": "Design approved. Final production has been queued.",
    }
