from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session, joinedload

from app.db.session import get_db
from app.models.order import Order, OrderStatus
from app.services.review_service import ReviewService, apply_creative_payload, now_utc

router = APIRouter(prefix="/api/review", tags=["protected-review"])


def _order(db: Session, order_id: str) -> Order:
    order = (
        db.query(Order)
        .options(joinedload(Order.client))
        .filter(Order.id == order_id)
        .first()
    )
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    return order


def _email(order: Order) -> str | None:
    client = getattr(order, "client", None)
    return getattr(client, "email", None) or getattr(order, "email", None)


def _require_token(order: Order, token: str | None, email: str | None = None) -> None:
    if not ReviewService.verify(order, token or "", email or _email(order)):
        raise HTTPException(status_code=403, detail="Invalid or expired review token")


@router.post("/{order_id}/prepare")
def prepare_review(order_id: str, db: Session = Depends(get_db)):
    """Create protected review metadata after production is ready.

    This does not deliver source files. It creates the private review URL and
    freezes the production payload used for review/revisions.
    """
    order = _order(db, order_id)
    email = _email(order)
    token = ReviewService.generate_token(str(order.id), email)
    review_url = ReviewService.build_review_url(str(order.id), email)

    apply_creative_payload(order)
    if hasattr(order, "review_token_hash"):
        order.review_token_hash = ReviewService.token_hash(token)
    if hasattr(order, "protected_preview_url"):
        order.protected_preview_url = review_url
    order.status = getattr(OrderStatus, "READY_FOR_REVIEW", "ready_for_review")
    db.commit()
    db.refresh(order)

    return {
        "ok": True,
        "order_id": order.id,
        "status": order.status,
        "review_url": review_url,
        "message": "Protected review link created. Final ZIP is still locked until final approval.",
    }


@router.get("/{order_id}")
def get_review(order_id: str, token: str = Query(...), email: str | None = None, db: Session = Depends(get_db)):
    order = _order(db, order_id)
    _require_token(order, token, email)
    allowed = getattr(order, "revision_rounds_allowed", None) or 2
    used = getattr(order, "revision_rounds_used", None) or 0
    return {
        "ok": True,
        "order_id": order.id,
        "status": order.status,
        "protected_preview_url": getattr(order, "protected_preview_url", None),
        "selected_design": {
            "id": getattr(order, "selected_design_id", None),
            "label": getattr(order, "selected_design_label", None),
            "url": getattr(order, "selected_design_url", None),
        },
        "production_payload": getattr(order, "production_payload", None) or {},
        "revision_rounds_allowed": allowed,
        "revision_rounds_used": used,
        "revision_rounds_remaining": max(0, allowed - used),
        "final_zip_locked": getattr(order, "final_approved_at", None) is None,
        "message": "Preview access is for review only. ZIP/source delivery is available after final approval.",
    }


@router.post("/{order_id}/revision")
def submit_revision(order_id: str, payload: dict, token: str = Query(...), email: str | None = None, db: Session = Depends(get_db)):
    order = _order(db, order_id)
    _require_token(order, token, email)
    allowed = getattr(order, "revision_rounds_allowed", None) or 2
    used = getattr(order, "revision_rounds_used", None) or 0
    if used >= allowed:
        raise HTTPException(status_code=400, detail="Revision limit reached for this package")

    revision = ReviewService.validate_revision_payload(payload)
    revisions = getattr(order, "revision_requests", None) or []
    if not isinstance(revisions, list):
        revisions = []
    revision["round_number"] = used + 1
    revisions.append(revision)

    if hasattr(order, "revision_requests"):
        order.revision_requests = revisions
    if hasattr(order, "revision_rounds_used"):
        order.revision_rounds_used = used + 1
    order.status = getattr(OrderStatus, "REVISION_REQUESTED", "revision_requested")
    db.commit()
    db.refresh(order)
    return {
        "ok": True,
        "order_id": order.id,
        "status": order.status,
        "revision_rounds_used": getattr(order, "revision_rounds_used", used + 1),
        "revision_rounds_remaining": max(0, allowed - (used + 1)),
        "message": "Revision request received as one grouped change list.",
    }


@router.post("/{order_id}/final-approval")
def final_approval(order_id: str, payload: dict | None = None, token: str = Query(...), email: str | None = None, db: Session = Depends(get_db)):
    order = _order(db, order_id)
    _require_token(order, token, email)
    if hasattr(order, "final_approved_at"):
        order.final_approved_at = now_utc()
    order.status = getattr(OrderStatus, "FINAL_APPROVED", "final_approved")
    db.commit()
    return {
        "ok": True,
        "order_id": order.id,
        "status": order.status,
        "message": "Final approval recorded. Final ZIP delivery can now be prepared.",
    }


@router.get("/{order_id}/final-zip")
def final_zip(order_id: str, token: str = Query(...), email: str | None = None, db: Session = Depends(get_db)):
    order = _order(db, order_id)
    _require_token(order, token, email)
    if getattr(order, "final_approved_at", None) is None:
        raise HTTPException(status_code=403, detail="Final ZIP is locked until final approval")
    return {
        "ok": True,
        "order_id": order.id,
        "status": order.status,
        "final_zip_url": getattr(order, "final_zip_url", None),
        "message": "Final ZIP delivery is unlocked after approval.",
    }
