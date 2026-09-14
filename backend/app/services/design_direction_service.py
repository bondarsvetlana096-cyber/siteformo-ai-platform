from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.journey.models import SiteFormoVisitor
from app.models.order import Order
from app.services.payment_boundary_service import (
    PaymentBoundaryError,
    has_post_design_activity,
    interaction_preference_record,
)
from app.services.q1_service import Q1OwnershipError, project_binding


DESIGN_DIRECTION_CONTRACT_VERSION = "v1"
DESIGN_DIRECTIONS = (
    ("clean-modern", "Clean Modern"),
    ("premium-business", "Premium Business"),
    ("bold-startup", "Bold Startup"),
    ("luxury-elite", "Luxury Elite"),
    ("tech-minimal", "Tech Minimal"),
    ("creative-studio", "Creative Studio"),
    ("nordic-soft", "Nordic Soft"),
    ("dark-contrast", "Dark Contrast"),
)
DESIGN_DIRECTION_KEYS = frozenset(key for key, _label in DESIGN_DIRECTIONS)


def _require_paid_v2_order(
    db: Session,
    visitor: SiteFormoVisitor,
    order_id: str,
    *,
    for_update: bool = False,
) -> Order:
    project_binding(db, visitor, order_id)
    statement = select(Order).where(Order.id == order_id)
    if for_update:
        statement = statement.with_for_update()
    order = db.execute(statement).scalar_one_or_none()
    if order is None:
        raise Q1OwnershipError("Project does not exist")
    if not isinstance((order.brief_answers or {}).get("q1_v2"), dict):
        raise PaymentBoundaryError("Design Direction is available only for Q1/Q2 V2 projects", 409)
    if not isinstance((order.extended_brief or {}).get("q2_v2"), dict):
        raise PaymentBoundaryError("Canonical Q2 V2 is required", 409)
    if order.deposit_status != "paid":
        raise PaymentBoundaryError("Verified deposit payment is required", 409)
    if not order.design_direction and has_post_design_activity(order):
        raise PaymentBoundaryError("Project has already moved beyond Design Direction", 409)
    return order


def _state(order: Order, *, idempotent: bool = False) -> dict[str, Any]:
    selected = order.design_direction
    return {
        "order_id": order.id,
        "stage": "design_direction_confirmed" if selected else "design_direction_required",
        "contract_version": DESIGN_DIRECTION_CONTRACT_VERSION,
        "available_directions": [
            {"key": key, "label": label} for key, label in DESIGN_DIRECTIONS
        ],
        "selected_direction": selected,
        # The existing schema has no suitable Design Direction confirmation timestamp.
        "confirmed_at": None,
        "next_step": (
            "post_payment_pending"
            if selected and interaction_preference_record(order)
            else "interaction_preference"
            if selected
            else "design_direction"
        ),
        "idempotent": idempotent,
    }


def get_design_direction_state(
    db: Session, visitor: SiteFormoVisitor, order_id: str,
) -> dict[str, Any]:
    return _state(_require_paid_v2_order(db, visitor, order_id))


def confirm_design_direction(
    db: Session, visitor: SiteFormoVisitor, order_id: str, direction: str,
) -> dict[str, Any]:
    if direction not in DESIGN_DIRECTION_KEYS:
        raise PaymentBoundaryError("Unknown Design Direction", 422)
    order = _require_paid_v2_order(db, visitor, order_id, for_update=True)
    if order.design_direction == direction:
        return _state(order, idempotent=True)
    if order.design_direction:
        raise PaymentBoundaryError(
            "Changing a confirmed Design Direction requires owner approval", 409,
        )
    order.design_direction = direction
    db.commit()
    db.refresh(order)
    return _state(order)
