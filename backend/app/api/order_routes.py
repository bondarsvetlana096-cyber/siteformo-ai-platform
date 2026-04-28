from __future__ import annotations

from datetime import datetime, timedelta, timezone
from html import escape
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session, joinedload

from app.core.config import settings
from app.db.session import get_db
from app.models.order import FinalPackage, Order, OrderStatus
from app.schemas.order import ApprovalResponse, IntakePayload, IntakeResponse
from app.services.approval_service import ApprovalService
from app.services.email_service import OwnerEmailComposer, send_email
from app.services.intake_service import IntakeService
from app.services.launch_link_service import LaunchLinkService

router = APIRouter(prefix="/api/orders", tags=["orders"])


# -----------------------------------------------------------------------------
# Safe helpers
# -----------------------------------------------------------------------------

def _now() -> datetime:
    return datetime.now(timezone.utc)


def _status(name: str, fallback: str | None = None) -> OrderStatus:
    """Return an OrderStatus safely while the enum is being migrated.

    This keeps this routes file deployable even if the model enum still uses old
    names such as FINAL_READY instead of the new SiteFormo production statuses.
    """
    value = getattr(OrderStatus, name, None)
    if value is not None:
        return value

    if fallback:
        value = getattr(OrderStatus, fallback, None)
        if value is not None:
            return value

    raise HTTPException(
        status_code=500,
        detail=f"OrderStatus.{name} is not defined. Update app.models.order.OrderStatus.",
    )


def _has_status(name: str) -> bool:
    return hasattr(OrderStatus, name)


def _set_if_exists(obj: Any, field_name: str, value: Any) -> None:
    if hasattr(obj, field_name):
        setattr(obj, field_name, value)


def _brief_to_markdown(answers: dict[str, Any]) -> str:
    if not answers:
        return "- Extended brief submitted without additional answers."

    lines: list[str] = []
    for key, value in answers.items():
        lines.append(f"- {key}: {value}")
    return "\n".join(lines)


def _concept_collection(order: Order) -> list[Any]:
    return list(getattr(order, "concepts", []) or [])


# -----------------------------------------------------------------------------
# Concept / preview generation placeholders
# -----------------------------------------------------------------------------

def _concept_html(order: Order, label: str, direction: str) -> str:
    topic = escape(
        order.business_name
        or order.source_url
        or order.desired_site_description
        or "Client business"
    )

    brief_answers = order.brief_answers or {}

    goal = escape(
        str(
            brief_answers.get(
                "main_goal",
                "turn visitors into qualified leads",
            )
        )
    )

    style = escape(str(brief_answers.get("style", direction)))

    return f"""
<section class="siteformo-hero siteformo-concept-{label.lower()}">
  <div class="siteformo-wrap">
    <p class="eyebrow">{style}</p>
    <h1>{topic}: a high-converting homepage built for trust and action</h1>
    <p>{goal}. The page should clearly explain the offer, reduce friction, and guide the visitor to one primary action.</p>
    <a class="primary-cta" href="#contact">Request a custom offer</a>
  </div>
</section>

<section class="siteformo-sections">
  <h2>Recommended structure</h2>
  <ul>
    <li>Hero with clear value proposition and CTA</li>
    <li>Problem / solution block</li>
    <li>Services or offer cards</li>
    <li>Trust, proof, and FAQ</li>
    <li>Final conversion section</li>
  </ul>
</section>
""".strip()


def _concept(order: Order, label: str, direction: str) -> dict[str, str]:
    return {
        "label": label,
        "art_direction": direction,
        "summary": (
            f"Design preview {label}: {direction}. "
            "Mobile-first, conversion-focused and ready for Divi production."
        ),
        "html": _concept_html(order, label, direction),
    }


def _build_preview_concepts(order: Order) -> list[dict[str, str]]:
    return [
        _concept(order, "A", "Main recommended direction: clean premium conversion design"),
        _concept(order, "B", "Variation: modern editorial layout with strong typography"),
        _concept(order, "C", "Variation: bold colorful startup-style landing page"),
        _concept(order, "D", "Variation: elegant minimal luxury-style website"),
        _concept(order, "E", "Variation: warm local-service design focused on trust"),
    ]


def _replace_order_concepts(db: Session, order: Order, concepts: list[dict[str, str]]) -> None:
    """Replace current order concepts with five preview concepts.

    The project already has an order.concepts relationship. To avoid guessing the
    exact model import name, this function reuses the existing related model class
    when concepts already exist. If the order has no concept rows yet, it falls
    back to IntakeService.save_concepts for the first two previews and then reuses
    the created class.
    """
    existing = _concept_collection(order)

    if not existing:
        IntakeService.save_concepts(db, order, concepts[0], concepts[1], keep_approved=False)
        db.flush()
        db.refresh(order)
        existing = _concept_collection(order)

    if not existing:
        raise HTTPException(
            status_code=500,
            detail="Could not create design preview records for this order.",
        )

    concept_cls = existing[0].__class__

    for old_concept in existing:
        db.delete(old_concept)

    db.flush()

    for item in concepts:
        db.add(
            concept_cls(
                order_id=order.id,
                concept_label=item["label"],
                art_direction=item["art_direction"],
                summary=item["summary"],
                html_code=item["html"],
            )
        )


def _serialize_concepts(order: Order) -> list[dict[str, Any]]:
    return [
        {
            "label": concept.concept_label,
            "art_direction": concept.art_direction,
            "summary": concept.summary,
            "html": concept.html_code,
            "preview_image_url": getattr(concept, "preview_image_url", None),
        }
        for concept in sorted(_concept_collection(order), key=lambda item: item.concept_label)
    ]


def _generate_logo_placeholders(order: Order, answers: dict[str, Any]) -> list[dict[str, str]]:
    wants_logo = bool(
        answers.get("logo_ordered")
        or answers.get("need_logo")
        or answers.get("logo_addon")
        or answers.get("logo") in {"no", "needed", "new"}
    )

    if not wants_logo:
        return []

    business_name = str(order.business_name or answers.get("company_name") or "SiteFormo Client")

    return [
        {"label": "Logo 1", "style": "Minimal premium wordmark", "business_name": business_name},
        {"label": "Logo 2", "style": "Modern icon plus wordmark", "business_name": business_name},
        {"label": "Logo 3", "style": "Elegant badge-style brand mark", "business_name": business_name},
    ]


# -----------------------------------------------------------------------------
# Order helpers
# -----------------------------------------------------------------------------

def _is_owner_bypass_order(order: Order) -> bool:
    client_email = getattr(getattr(order, "client", None), "email", None)
    return LaunchLinkService.should_bypass_payment_approval(client_email)


def _get_order_or_404(db: Session, order_id: str) -> Order:
    order = (
        db.query(Order)
        .options(
            joinedload(Order.concepts),
            joinedload(Order.client),
        )
        .filter(Order.id == order_id)
        .first()
    )

    if not order:
        raise HTTPException(status_code=404, detail="Order not found")

    return order


def _get_existing_final_package(order: Order) -> FinalPackage | None:
    packages = getattr(order, "final_packages", None)
    if not packages:
        return None
    return packages[-1]


def _build_final_package_from_selected_design(
    db: Session,
    order: Order,
    selected_label: str,
    note: str,
) -> None:
    existing_package = _get_existing_final_package(order)

    if existing_package:
        order.status = _status("FULL_PRODUCTION_STARTED", "FINAL_READY")
        return

    concept = (
        next(
            (item for item in _concept_collection(order) if item.concept_label == selected_label),
            None,
        )
        or (_concept_collection(order)[0] if _concept_collection(order) else None)
    )

    divi_html = (
        concept.html_code
        if concept
        else _concept_html(order, selected_label, "Selected design direction")
    )

    db.add(
        FinalPackage(
            order_id=order.id,
            selected_concept_label=selected_label,
            divi_html=divi_html,
            brief_markdown=_brief_to_markdown(order.brief_answers or {}),
            notes=note,
        )
    )

    order.status = _status("FULL_PRODUCTION_STARTED", "FINAL_READY")


# -----------------------------------------------------------------------------
# Routes
# -----------------------------------------------------------------------------

@router.post("/intake", response_model=IntakeResponse)
def create_order_intake(
    payload: IntakePayload,
    db: Session = Depends(get_db),
):
    order, reused_context, reused_order_id = IntakeService.create_order(db, payload)
    owner_bypass = LaunchLinkService.should_bypass_payment_approval(payload.email)

    if not _concept_collection(order):
        IntakeService.save_concepts(
            db,
            order,
            _concept(order, "A", "Clean premium conversion page"),
            _concept(order, "B", "Modern editorial landing page"),
            keep_approved=owner_bypass,
        )
        db.flush()
        db.refresh(order)

    if owner_bypass:
        order.status = _status("APPROVED", "FINAL_READY")
        order.approved_at = order.approved_at or _now()
        db.commit()
        db.refresh(order)

    return IntakeResponse(
        client_id=order.client_id,
        order_id=order.id,
        reused_context=reused_context,
        reused_order_id=reused_order_id,
        recommended_tier=order.recommended_tier,
        estimated_price_eur=order.estimated_price_eur,
        pricing_reasoning=order.pricing_reasoning or "",
        preferred_language=order.preferred_language,
        status=order.status,
        owner_bypass=owner_bypass,
        payment_required=not owner_bypass,
    )


@router.get("/confirm")
def confirm_paid_order(
    order_id: str = Query(...),
    db: Session = Depends(get_db),
):
    order = _get_order_or_404(db, order_id)

    if _is_owner_bypass_order(order):
        order.status = _status("APPROVED", "FINAL_READY")
        order.approved_at = order.approved_at or _now()
        message = "Owner order confirmed. Extended questionnaire is unlocked."
    else:
        # Stripe payment confirmation should unlock the extended questionnaire.
        # It must NOT start preview generation or final generation.
        order.status = _status("APPROVED", "PENDING_PAYMENT_APPROVAL")
        order.approved_at = order.approved_at or _now()
        message = "Deposit received. Extended questionnaire is now available."

    db.commit()
    db.refresh(order)

    return {
        "status": "confirmed",
        "order_id": order.id,
        "order_status": order.status,
        "message": message,
    }


@router.get("/{order_id}")
def get_order(
    order_id: str,
    db: Session = Depends(get_db),
):
    order = _get_order_or_404(db, order_id)
    owner_bypass = _is_owner_bypass_order(order)
    estimated_price = order.estimated_price_eur or 0

    if order.status == _status("APPROVED", "PENDING_PAYMENT_APPROVAL"):
        next_step = "Extended questionnaire is available."
    elif _has_status("DESIGN_PREVIEWS_READY") and order.status == OrderStatus.DESIGN_PREVIEWS_READY:
        next_step = "Design previews are ready. Client should select one design."
    elif _has_status("AWAITING_CLIENT_DESIGN_CHOICE") and order.status == OrderStatus.AWAITING_CLIENT_DESIGN_CHOICE:
        next_step = "Waiting for the client to select one design."
    elif _has_status("FULL_PRODUCTION_STARTED") and order.status == OrderStatus.FULL_PRODUCTION_STARTED:
        next_step = "Selected design approved. Full production has started."
    else:
        next_step = "Pay 50% deposit. After payment, the extended questionnaire becomes available."

    return {
        "order_id": order.id,
        "status": order.status,
        "owner_bypass": owner_bypass,
        "payment_required": not owner_bypass,
        "recommended_tier": order.recommended_tier,
        "estimated_price_eur": estimated_price,
        "deposit_due_eur": 0 if owner_bypass else int(estimated_price / 2),
        "pricing_reasoning": order.pricing_reasoning,
        "reused_context_from_order_id": order.reused_context_from_order_id,
        "concepts": _serialize_concepts(order),
        "brief_answers": order.brief_answers or {},
        "next_step": next_step,
    }


@router.post("/{order_id}/payment-reported")
async def payment_reported(
    order_id: str,
    db: Session = Depends(get_db),
):
    order = _get_order_or_404(db, order_id)

    if _is_owner_bypass_order(order):
        order.status = _status("APPROVED", "FINAL_READY")
        order.approved_at = order.approved_at or _now()
        db.commit()
        db.refresh(order)

        return {
            "order_id": order.id,
            "status": order.status,
            "message": "Owner email detected. Payment approval was skipped.",
        }

    order.status = _status("PENDING_PAYMENT_APPROVAL", "APPROVED")
    db.commit()
    db.refresh(order)

    email = OwnerEmailComposer.compose_order_email(order)
    await send_email(email["to"], email["subject"], email["html"])

    return {
        "order_id": order.id,
        "status": order.status,
        "message": "Payment report received. SiteFormo will verify the deposit.",
    }


@router.get("/{order_id}/decision", response_model=ApprovalResponse)
def decision(
    order_id: str,
    action: str = Query(...),
    token: str = Query(...),
    db: Session = Depends(get_db),
):
    order = db.query(Order).filter(Order.id == order_id).first()

    if not order:
        raise HTTPException(status_code=404, detail="Order not found")

    if not ApprovalService.verify(order_id, action, token):
        raise HTTPException(status_code=403, detail="Invalid approval token")

    return _apply_decision(db, order, action)


@router.post("/{order_id}/decision/manual", response_model=ApprovalResponse)
def manual_decision(
    order_id: str,
    action: str = Query(...),
    db: Session = Depends(get_db),
):
    if not settings.allow_manual_decision_without_token:
        raise HTTPException(status_code=403, detail="Manual decisions are disabled")

    order = db.query(Order).filter(Order.id == order_id).first()

    if not order:
        raise HTTPException(status_code=404, detail="Order not found")

    return _apply_decision(db, order, action)


def _apply_decision(
    db: Session,
    order: Order,
    action: str,
) -> ApprovalResponse:
    now = _now()

    if action == "approve":
        order.status = _status("APPROVED", "PENDING_PAYMENT_APPROVAL")
        order.approved_at = now
        message = "Payment approved. The extended questionnaire can now be shown to the client."
    elif action == "reject":
        order.status = _status("REJECTED", "PENDING_PAYMENT_APPROVAL")
        order.rejected_at = now
        message = "Payment was not approved. Hold the project and contact the client manually."
    else:
        raise HTTPException(status_code=400, detail="action must be approve or reject")

    db.commit()
    db.refresh(order)

    return ApprovalResponse(order_id=order.id, status=order.status, message=message)


@router.post("/{order_id}/extended-brief")
async def submit_extended_brief(
    order_id: str,
    answers: dict[str, Any],
    db: Session = Depends(get_db),
):
    """Save the extended questionnaire and prepare five design previews.

    Important: this endpoint must NOT create the final package. The client has
    only submitted the extended brief. The next step is design preview selection.
    """
    order = _get_order_or_404(db, order_id)
    owner_bypass = _is_owner_bypass_order(order)

    allowed_statuses = {_status("APPROVED", "FINAL_READY")}
    if _has_status("BRIEF_SUBMITTED"):
        allowed_statuses.add(OrderStatus.BRIEF_SUBMITTED)
    if _has_status("DESIGN_PREVIEWS_READY"):
        allowed_statuses.add(OrderStatus.DESIGN_PREVIEWS_READY)
    if _has_status("AWAITING_CLIENT_DESIGN_CHOICE"):
        allowed_statuses.add(OrderStatus.AWAITING_CLIENT_DESIGN_CHOICE)

    if order.status not in allowed_statuses and not owner_bypass:
        raise HTTPException(
            status_code=409,
            detail="Deposit payment must be approved before the extended questionnaire can be submitted.",
        )

    order.brief_answers = answers
    order.status = _status("BRIEF_SUBMITTED", "APPROVED")
    db.flush()

    preview_concepts = _build_preview_concepts(order)
    _replace_order_concepts(db, order, preview_concepts)

    logo_options = _generate_logo_placeholders(order, answers)
    if logo_options:
        answers["generated_logo_options"] = logo_options
        order.brief_answers = answers

    order.status = _status("AWAITING_CLIENT_DESIGN_CHOICE", "APPROVED")
    _set_if_exists(order, "design_previews_ready_at", _now())

    db.commit()
    db.refresh(order)

    # Keep this email safe: older email_service versions may not have a dedicated
    # preview email composer yet. Owner gets notified; client email can be added
    # in email_service as compose_design_preview_email(order).
    if hasattr(OwnerEmailComposer, "compose_design_preview_email"):
        email = OwnerEmailComposer.compose_design_preview_email(order)
        await send_email(email["to"], email["subject"], email["html"])
    else:
        brief_markdown = _brief_to_markdown(answers)
        email = OwnerEmailComposer.compose_delivery_email(order, brief_markdown)
        await send_email(email["to"], email["subject"], email["html"])

    return {
        "order_id": order.id,
        "status": order.status,
        "concepts": _serialize_concepts(order),
        "logos": logo_options,
        "message": "Extended questionnaire saved. Five design previews are ready for client selection.",
    }


@router.post("/{order_id}/approve-design")
async def approve_design(
    order_id: str,
    payload: dict[str, Any],
    db: Session = Depends(get_db),
):
    """Client selects one preview and starts the one-hour refund window."""
    order = _get_order_or_404(db, order_id)

    selected_label = (
        payload.get("selected_concept_label")
        or payload.get("concept_label")
        or payload.get("selected_design")
        or "A"
    )

    valid_labels = {concept.concept_label for concept in _concept_collection(order)}
    if valid_labels and selected_label not in valid_labels:
        raise HTTPException(
            status_code=400,
            detail=f"selected_concept_label must be one of: {', '.join(sorted(valid_labels))}",
        )

    now = _now()
    refund_until = now + timedelta(hours=1)

    _set_if_exists(order, "selected_concept_label", selected_label)
    _set_if_exists(order, "selected_design_label", selected_label)
    _set_if_exists(order, "selected_screenshot_url", payload.get("selected_screenshot_url"))
    _set_if_exists(order, "design_approved_at", now)
    _set_if_exists(order, "refund_window_started_at", now)
    _set_if_exists(order, "refund_window_expires_at", refund_until)

    if _has_status("DESIGN_APPROVED"):
        order.status = OrderStatus.DESIGN_APPROVED
        db.flush()

    _build_final_package_from_selected_design(
        db,
        order,
        selected_label,
        "Client selected a design preview. One-hour refund window started; full production can begin.",
    )

    db.commit()
    db.refresh(order)

    return {
        "order_id": order.id,
        "status": order.status,
        "selected_concept_label": selected_label,
        "refund_window_started_at": now.isoformat(),
        "refund_window_expires_at": refund_until.isoformat(),
        "message": "Design approved. One-hour refund window has started and full production has been queued.",
    }
