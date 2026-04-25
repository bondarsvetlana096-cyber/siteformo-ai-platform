from __future__ import annotations

from datetime import datetime, timezone
from html import escape

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session, joinedload

from app.core.config import settings
from app.db.session import get_db
from app.models.order import FinalPackage, HomepageConcept, Order, OrderStatus
from app.schemas.order import ApprovalResponse, IntakePayload, IntakeResponse
from app.services.approval_service import ApprovalService
from app.services.email_service import OwnerEmailComposer, send_email
from app.services.intake_service import IntakeService
from app.services.launch_link_service import LaunchLinkService

router = APIRouter(prefix="/api/orders", tags=["orders"])


def _concept_html(order: Order, label: str, direction: str) -> str:
    topic = escape(order.business_name or order.source_url or order.desired_site_description or "Client business")
    goal = escape((order.brief_answers or {}).get("main_goal", "turn visitors into qualified leads"))
    style = escape((order.brief_answers or {}).get("style", direction))
    return f"""<section class=\"siteformo-hero siteformo-concept-{label.lower()}\">
  <div class=\"siteformo-wrap\">
    <p class=\"eyebrow\">{style}</p>
    <h1>{topic}: a high-converting homepage built for trust and action</h1>
    <p>{goal}. The page should clearly explain the offer, reduce friction, and guide the visitor to one primary action.</p>
    <a class=\"primary-cta\" href=\"#contact\">Request a custom offer</a>
  </div>
</section>
<section class=\"siteformo-sections\">
  <h2>Recommended structure</h2>
  <ul>
    <li>Hero with clear value proposition and CTA</li>
    <li>Problem / solution block</li>
    <li>Services or offer cards</li>
    <li>Trust, proof, and FAQ</li>
    <li>Final conversion section</li>
  </ul>
</section>"""


def _concept(order: Order, label: str, direction: str) -> dict:
    return {
        "art_direction": direction,
        "summary": f"Concept {label}: {direction}. Built for mobile-first conversion and Divi 5 editing.",
        "html": _concept_html(order, label, direction),
    }


def _serialize_concepts(order: Order) -> list[dict]:
    return [
        {
            "label": c.concept_label,
            "art_direction": c.art_direction,
            "summary": c.summary,
            "html": c.html_code,
        }
        for c in sorted(order.concepts, key=lambda item: item.concept_label)
    ]


def _is_owner_bypass_order(order: Order) -> bool:
    client_email = getattr(getattr(order, "client", None), "email", None)
    return LaunchLinkService.should_bypass_payment_approval(client_email)


def _build_final_package_from_current_brief(db: Session, order: Order, note: str) -> None:
    selected = "A"
    concept = next((c for c in order.concepts if c.concept_label == selected), None) or (order.concepts[0] if order.concepts else None)
    divi_html = concept.html_code if concept else _concept_html(order, "A", "Owner bypass ready page")
    brief_answers = order.brief_answers or {}
    brief_markdown = "\n".join(f"- {key}: {value}" for key, value in brief_answers.items()) or "- Generated from the first SiteFormo brief."

    db.add(FinalPackage(
        order_id=order.id,
        selected_concept_label=selected,
        divi_html=divi_html,
        brief_markdown=brief_markdown,
        notes=note,
    ))
    order.status = OrderStatus.FINAL_READY
    order.approved_at = datetime.now(timezone.utc)


@router.post("/intake", response_model=IntakeResponse)
def create_order_intake(payload: IntakePayload, db: Session = Depends(get_db)):
    order, reused_context, reused_order_id = IntakeService.create_order(db, payload)
    owner_bypass = LaunchLinkService.should_bypass_payment_approval(payload.email)

    if not order.concepts:
        IntakeService.save_concepts(
            db,
            order,
            _concept(order, "A", "Clean premium conversion page"),
            _concept(order, "B", "Modern editorial landing page"),
            keep_approved=owner_bypass,
        )
        db.refresh(order)

    if owner_bypass and order.status != OrderStatus.FINAL_READY:
        _build_final_package_from_current_brief(
            db,
            order,
            "Owner bypass: klon97048@gmail.com can generate immediately without deposit approval.",
        )
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


@router.get("/{order_id}")
def get_order(order_id: str, db: Session = Depends(get_db)):
    order = db.query(Order).options(joinedload(Order.concepts), joinedload(Order.client)).filter(Order.id == order_id).first()
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    owner_bypass = _is_owner_bypass_order(order)
    next_step = "Final package is ready. Owner bypass skipped payment approval." if owner_bypass and order.status == OrderStatus.FINAL_READY else "Pay 50% deposit. After owner approval, the extended brief becomes available."
    return {
        "order_id": order.id,
        "status": order.status,
        "owner_bypass": owner_bypass,
        "payment_required": not owner_bypass,
        "recommended_tier": order.recommended_tier,
        "estimated_price_eur": order.estimated_price_eur,
        "deposit_due_eur": 0 if owner_bypass else int(order.estimated_price_eur / 2),
        "pricing_reasoning": order.pricing_reasoning,
        "reused_context_from_order_id": order.reused_context_from_order_id,
        "concepts": _serialize_concepts(order),
        "next_step": next_step,
    }


@router.post("/{order_id}/payment-reported")
async def payment_reported(order_id: str, db: Session = Depends(get_db)):
    order = db.query(Order).options(joinedload(Order.concepts), joinedload(Order.client)).filter(Order.id == order_id).first()
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")

    if _is_owner_bypass_order(order):
        if order.status != OrderStatus.FINAL_READY:
            _build_final_package_from_current_brief(
                db,
                order,
                "Owner bypass: payment report ignored because owner email does not require deposit approval.",
            )
            db.commit()
        return {
            "order_id": order.id,
            "status": order.status,
            "message": "Owner email detected. Payment approval was skipped and the final package is ready.",
        }

    order.status = OrderStatus.PENDING_PAYMENT_APPROVAL
    db.commit()
    email = OwnerEmailComposer.compose_order_email(order)
    await send_email(email["to"], email["subject"], email["html"])
    return {
        "order_id": order.id,
        "status": order.status,
        "message": "Payment report received. SiteFormo will verify the deposit and unlock the extended brief after approval.",
    }


@router.get("/{order_id}/decision", response_model=ApprovalResponse)
def decision(order_id: str, action: str = Query(...), token: str = Query(...), db: Session = Depends(get_db)):
    order = db.query(Order).filter(Order.id == order_id).first()
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    if not ApprovalService.verify(order_id, action, token):
        raise HTTPException(status_code=403, detail="Invalid approval token")
    return _apply_decision(db, order, action)


@router.post("/{order_id}/decision/manual", response_model=ApprovalResponse)
def manual_decision(order_id: str, action: str = Query(...), db: Session = Depends(get_db)):
    if not settings.allow_manual_decision_without_token:
        raise HTTPException(status_code=403, detail="Manual decisions are disabled")
    order = db.query(Order).filter(Order.id == order_id).first()
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    return _apply_decision(db, order, action)


def _apply_decision(db: Session, order: Order, action: str) -> ApprovalResponse:
    now = datetime.now(timezone.utc)
    if action == "approve":
        order.status = OrderStatus.APPROVED
        order.approved_at = now
        message = "Payment approved. The extended questionnaire can now be shown to the client."
    elif action == "reject":
        order.status = OrderStatus.REJECTED
        order.rejected_at = now
        message = "Payment was not approved. Hold generation and contact the client manually."
    else:
        raise HTTPException(status_code=400, detail="action must be approve or reject")
    db.commit()
    return ApprovalResponse(order_id=order.id, status=order.status, message=message)


@router.post("/{order_id}/extended-brief")
async def submit_extended_brief(order_id: str, answers: dict, db: Session = Depends(get_db)):
    order = db.query(Order).options(joinedload(Order.concepts), joinedload(Order.client)).filter(Order.id == order_id).first()
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    owner_bypass = _is_owner_bypass_order(order)
    if order.status not in {OrderStatus.APPROVED, OrderStatus.FINAL_READY} and not owner_bypass:
        raise HTTPException(status_code=409, detail="Owner payment approval is required before final generation")

    brief_markdown = "\n".join(f"- {key}: {value}" for key, value in answers.items())
    selected = answers.get("selected_concept_label") or "A"
    concept = next((c for c in order.concepts if c.concept_label == selected), None) or (order.concepts[0] if order.concepts else None)
    divi_html = concept.html_code if concept else _concept_html(order, "A", "Final Divi-ready page")
    package = FinalPackage(order_id=order.id, selected_concept_label=selected, divi_html=divi_html, brief_markdown=brief_markdown, notes="Generated from extended brief after owner payment approval or owner bypass.")
    db.add(package)
    order.status = OrderStatus.FINAL_READY
    if owner_bypass and not order.approved_at:
        order.approved_at = datetime.now(timezone.utc)
    db.commit()
    email = OwnerEmailComposer.compose_delivery_email(order, brief_markdown)
    await send_email(email["to"], email["subject"], email["html"])
    return {"order_id": order.id, "status": order.status, "message": "Final Divi 5 package was generated and sent to the owner for visual review."}
