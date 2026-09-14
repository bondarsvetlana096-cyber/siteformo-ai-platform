from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
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


INTERACTION_PREFERENCE_CONTRACT_VERSION = "v1"
INTERACTION_PREFERENCES = (
    ("subtle", "Subtle", False),
    ("recommended", "Recommended", True),
    ("more_expressive", "More expressive", False),
)
INTERACTION_PREFERENCE_KEYS = frozenset(key for key, _label, _recommended in INTERACTION_PREFERENCES)


def _require_interaction_stage(
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
        raise PaymentBoundaryError("Interaction Preference is available only for Q1/Q2 V2 projects", 409)
    if not isinstance((order.extended_brief or {}).get("q2_v2"), dict):
        raise PaymentBoundaryError("Canonical Q2 V2 is required", 409)
    if order.deposit_status != "paid":
        raise PaymentBoundaryError("Verified deposit payment is required", 409)
    if not order.design_direction:
        raise PaymentBoundaryError("Confirmed Design Direction is required", 409)
    if interaction_preference_record(order) is None and has_post_design_activity(order):
        raise PaymentBoundaryError("Project has already moved beyond Interaction Preference", 409)
    return order


def _state(order: Order, *, idempotent: bool = False) -> dict[str, Any]:
    record = interaction_preference_record(order)
    selected = record.get("value") if record else None
    return {
        "order_id": order.id,
        "stage": "interaction_preference_confirmed" if selected else "interaction_preference_required",
        "contract_version": INTERACTION_PREFERENCE_CONTRACT_VERSION,
        "available_preferences": [
            {"key": key, "label": label, "recommended": recommended}
            for key, label, recommended in INTERACTION_PREFERENCES
        ],
        "selected_preference": selected,
        "confirmed_at": record.get("confirmed_at") if record else None,
        "next_step": "post_payment_pending" if selected else "interaction_preference",
        "idempotent": idempotent,
    }


def get_interaction_preference(
    db: Session, visitor: SiteFormoVisitor, order_id: str,
) -> dict[str, Any]:
    return _state(_require_interaction_stage(db, visitor, order_id))


def confirm_interaction_preference(
    db: Session, visitor: SiteFormoVisitor, order_id: str, preference: str,
) -> dict[str, Any]:
    if preference not in INTERACTION_PREFERENCE_KEYS:
        raise PaymentBoundaryError("Unknown Interaction Preference", 422)
    order = _require_interaction_stage(db, visitor, order_id, for_update=True)
    existing = interaction_preference_record(order)
    if existing and existing.get("value") == preference:
        return _state(order, idempotent=True)
    if existing:
        raise PaymentBoundaryError(
            "Changing a confirmed Interaction Preference requires owner approval", 409,
        )

    extended = deepcopy(order.extended_brief or {})
    post_payment = deepcopy(extended.get("post_payment_v2") or {})
    post_payment["interaction_preference"] = {
        "contract_version": INTERACTION_PREFERENCE_CONTRACT_VERSION,
        "value": preference,
        "confirmed_at": datetime.now(timezone.utc).isoformat(),
    }
    extended["post_payment_v2"] = post_payment
    order.extended_brief = extended
    db.commit()
    db.refresh(order)
    return _state(order)
