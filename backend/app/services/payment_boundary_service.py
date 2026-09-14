from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.journey.models import SiteFormoVisitor
from app.models.order import Order, OrderStatus
from app.models.payment import PaymentAttempt, StripeWebhookEvent
from app.schemas.order import Q2V2Payload
from app.services.q1_service import Q1OwnershipError, project_binding
from app.services.q2_scope_planner import PACKAGE_RULE_VERSION, qualify_scope
from app.services.email_service import compose_prepayment_summary_email


PACKAGE_PRICES_CENTS = {
    "starter": 90_000,
    "business": 150_000,
    "reference": None,
    "advanced": 450_000,
}
ADDON_REGISTRY_CENTS = {
    "additional_page": 12_000,
    "video_entry_page": 12_000,
    "simple_logo": 10_000,
    "installation": 12_500,
}
ACTIVE_ATTEMPT_STATUSES = {"creating", "checkout_created", "pending"}
SCOPE_VERSION = "payment_scope_v2"
SUMMARY_VERSION = "prepayment_summary_v1"
CURRENCY = "EUR"
INTERACTION_PREFERENCE_VALUES = frozenset({"subtle", "recommended", "more_expressive"})


def interaction_preference_record(order: Order) -> dict[str, Any] | None:
    extended = order.extended_brief or {}
    post_payment = extended.get("post_payment_v2")
    record = post_payment.get("interaction_preference") if isinstance(post_payment, dict) else None
    if not isinstance(record, dict):
        return None
    if record.get("contract_version") != "v1" or record.get("value") not in INTERACTION_PREFERENCE_VALUES:
        return None
    if not isinstance(record.get("confirmed_at"), str) or not record["confirmed_at"]:
        return None
    return record


def has_post_design_activity(order: Order) -> bool:
    """Return true when an Order has already entered a later legacy/design stage."""
    return any((
        order.design_status,
        order.generation_status,
        order.full_generation_started_at,
        order.design_approved_at,
        order.refund_window_started_at,
        order.refund_window_expires_at,
        order.selected_design_id,
        order.selected_design_label,
        order.selected_design_url,
        order.selected_screenshot_url,
        order.interaction_style,
        order.production_payload,
    ))


class PaymentBoundaryError(RuntimeError):
    def __init__(self, message: str, status_code: int = 409):
        super().__init__(message)
        self.status_code = status_code


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _utc(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def approved_legal_terms_version() -> str | None:
    value = os.getenv("SITEFORMO_LEGAL_TERMS_VERSION", "").strip()
    return value or None


def package_price_cents(package: str) -> int:
    if package not in PACKAGE_PRICES_CENTS:
        raise PaymentBoundaryError("Qualified package is unavailable")
    price = PACKAGE_PRICES_CENTS[package]
    if price is None:
        raise PaymentBoundaryError("Reference checkout is blocked: authoritative price required")
    return price


def confirm_brief_and_legal(
    db: Session, visitor: SiteFormoVisitor, order_id: str, *,
    brief_confirmed: bool, legal_confirmed: bool, legal_terms_version: str | None,
) -> Order:
    project_binding(db, visitor, order_id)
    order = db.execute(select(Order).where(Order.id == order_id).with_for_update()).scalar_one_or_none()
    if order is None or order.status != OrderStatus.DRAFT:
        raise Q1OwnershipError("Only the current draft project may be confirmed")
    q2 = (order.extended_brief or {}).get("q2_v2")
    if not isinstance(q2, dict):
        raise PaymentBoundaryError("Canonical Q2 V2 must be saved first", 409)
    now = _now()
    if brief_confirmed:
        order.brief_confirmed_at = order.brief_confirmed_at or now
    if legal_confirmed:
        approved = approved_legal_terms_version()
        if approved is None:
            raise PaymentBoundaryError("Production legal terms version is not configured", 503)
        if legal_terms_version != approved:
            raise PaymentBoundaryError("Legal terms version is not approved", 409)
        if order.legal_terms_version != legal_terms_version:
            order.legal_confirmed_at = now
        else:
            order.legal_confirmed_at = order.legal_confirmed_at or now
        order.legal_terms_version = legal_terms_version
    if order.prepayment_summary_email_status != "sent":
        order.prepayment_summary_email_status = "pending"
    db.commit()
    db.refresh(order)
    return order


def _canonical_q2(order: Order, *, require_checkout_ready: bool = True) -> tuple[dict[str, Any], dict[str, Any]]:
    extended = order.extended_brief or {}
    stored = extended.get("q2_v2")
    if not isinstance(stored, dict):
        raise PaymentBoundaryError("Canonical Q2 V2 is required")
    authored = {key: value for key, value in stored.items() if key != "scope_qualification"}
    payload = Q2V2Payload.model_validate(authored)
    qualification = qualify_scope(payload)
    if qualification["eligibility"] != "supported" or not qualification.get("recommended_package"):
        raise PaymentBoundaryError("Q2 scope is not resolved for payment")
    if require_checkout_ready and not qualification["checkout_ready"]:
        raise PaymentBoundaryError("Q2 scope is not checkout ready")
    package = qualification.get("recommended_package")
    if package not in PACKAGE_PRICES_CENTS:
        raise PaymentBoundaryError("Qualified package is unavailable")
    return authored, qualification


def _confirmed_addons(q2: dict[str, Any], confirmed_at: datetime) -> list[dict[str, Any]]:
    addons: list[dict[str, Any]] = []
    labels = {
        "additional_page": "Additional page",
        "video_entry_page": "Video Entry Page",
        "simple_logo": "Simple SiteFormo Logo",
        "installation": "Installation",
    }
    confirmed_at_value = confirmed_at.isoformat()
    for item in q2.get("paid_structural_options") or []:
        key = item.get("key")
        if item.get("explicit_confirmed") and key in {"additional_page", "video_entry_page"}:
            quantity = int(item.get("quantity") or 1)
            for _ in range(quantity):
                addons.append({"addon_key": key, "description": labels[key], "confirmed_price_cents": ADDON_REGISTRY_CENTS[key], "confirmed_at": confirmed_at_value, "charge_phase": "final_balance"})
    if (q2.get("logo") or {}).get("choice") == "siteformo_simple_logo":
        addons.append({"addon_key": "simple_logo", "description": labels["simple_logo"], "confirmed_price_cents": ADDON_REGISTRY_CENTS["simple_logo"], "confirmed_at": confirmed_at_value, "charge_phase": "final_balance"})
    if (q2.get("delivery_context") or {}).get("installation_selected"):
        addons.append({"addon_key": "installation", "description": labels["installation"], "confirmed_price_cents": ADDON_REGISTRY_CENTS["installation"], "confirmed_at": confirmed_at_value, "charge_phase": "final_balance"})
    return addons


def _scope_calculation(
    order: Order, visitor: SiteFormoVisitor, *, require_checkout_ready: bool = False,
) -> dict[str, Any]:
    """Single financial calculation used by summary, email, and Checkout."""
    q2, qualification = _canonical_q2(order, require_checkout_ready=require_checkout_ready)
    package = qualification["recommended_package"]
    base = package_price_cents(package)
    confirmed_at = order.brief_confirmed_at or order.updated_at or order.created_at or _now()
    addons = _confirmed_addons(q2, confirmed_at)
    deposit = base // 2
    addon_balance = sum(item["confirmed_price_cents"] for item in addons)
    q2_canonical = json.dumps(q2, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    q2_hash = hashlib.sha256(q2_canonical.encode()).hexdigest()
    scope_basis = {
        "q2_content_hash": q2_hash,
        "scope_qualification_version": qualification["rule_version"],
        "base_package": package,
        "base_package_price_cents": base,
        "confirmed_addons": [
            {key: value for key, value in item.items() if key != "confirmed_at"}
            for item in addons
        ],
        "scope_version": SCOPE_VERSION,
    }
    scope_hash = hashlib.sha256(
        json.dumps(scope_basis, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
    ).hexdigest()
    return {
        "q2": q2,
        "qualification": qualification,
        "q2_content_hash": q2_hash,
        "scope_hash": scope_hash,
        "base_package": package,
        "base_package_price_cents": base,
        "initial_deposit_rate": "0.50",
        "initial_deposit_amount_cents": deposit,
        "currency": CURRENCY,
        "confirmed_addons": addons,
        "addons_due_now_cents": 0,
        "remaining_base_package_balance_cents": base - deposit,
        "confirmed_addons_balance_cents": addon_balance,
        "current_final_balance_cents": base - deposit + addon_balance,
    }


def build_prepayment_summary(order: Order, visitor: SiteFormoVisitor) -> dict[str, Any]:
    calculation = _scope_calculation(order, visitor)
    q2 = calculation["q2"]
    qualification = calculation["qualification"]
    identity = q2.get("business_identity") or {}
    activity = q2.get("business_activity") or {}
    architecture = qualification.get("provisional_architecture") or {}
    email_meta = (order.extended_brief or {}).get("payment_boundary_v2_email") or {}
    approved_terms = approved_legal_terms_version()
    email_signature = _email_scope_signature(calculation["scope_hash"], order.legal_terms_version)
    email_current = (
        email_meta.get("signature") == email_signature
        and order.legal_terms_version == approved_terms
    )
    email_status = order.prepayment_summary_email_status if email_current else "pending"
    email_sent_at = order.prepayment_summary_email_sent_at if email_current and email_status == "sent" else None
    return {
        "order_id": order.id,
        "project_summary": {
            "business_identity": {"status": identity.get("status"), "name": identity.get("name")},
            "business_activity": {"short_niche": activity.get("niche"), "broad_model": activity.get("broad_model")},
            "audience": q2.get("audience"),
            "primary_goal": q2.get("primary_goal"),
            "confirmed_functions": [item.get("key") for item in q2.get("functions") or [] if item.get("confirmed")],
            "provisional_architecture": {
                "status": architecture.get("status"),
                "minimum_coherent_pages": architecture.get("minimum_coherent_pages"),
                "proposed_page_roles": architecture.get("proposed_page_roles") or [],
                "reasoning_codes": architecture.get("reasoning_codes") or [],
            },
            "package_qualification": {
                "eligibility": qualification.get("eligibility"),
                "minimum_package": qualification.get("minimum_package"),
                "recommended_package": qualification.get("recommended_package"),
                "required_floor_reasons": qualification.get("required_floor_reasons") or [],
                "optional_recommendation_reasons": qualification.get("optional_recommendation_reasons") or [],
            },
        },
        "payment_summary": {
            key: calculation[key] for key in (
                "base_package", "base_package_price_cents", "initial_deposit_rate",
                "initial_deposit_amount_cents", "currency", "addons_due_now_cents",
                "remaining_base_package_balance_cents", "confirmed_addons_balance_cents",
                "current_final_balance_cents",
            )
        } | {
            "confirmed_addons": [
                {
                    "addon_key": item["addon_key"], "label": item["description"],
                    "confirmed_price_cents": item["confirmed_price_cents"],
                    "charge_phase": item["charge_phase"],
                }
                for item in calculation["confirmed_addons"]
            ]
        },
        "confirmation_state": {
            "package_and_addons_confirmed": bool(q2.get("package_and_addons_confirmed")),
            "brief_confirmed_at": order.brief_confirmed_at,
            "legal_confirmed_at": order.legal_confirmed_at,
            "legal_terms_version": order.legal_terms_version,
        },
        "email_state": {"status": email_status, "sent_at": email_sent_at},
        "scope": {
            "q2_schema_version": q2["schema_version"],
            "scope_version": SCOPE_VERSION,
            "scope_hash": calculation["scope_hash"],
            "summary_version": SUMMARY_VERSION,
        },
        "legal_terms_available": approved_terms is not None,
        "approved_legal_terms_version": approved_terms,
    }


def prepayment_summary(db: Session, visitor: SiteFormoVisitor, order_id: str) -> dict[str, Any]:
    project_binding(db, visitor, order_id)
    order = db.get(Order, order_id)
    if order is None or order.status != OrderStatus.DRAFT:
        raise Q1OwnershipError("Only the current draft project has a pre-payment summary")
    if not isinstance((order.brief_answers or {}).get("q1_v2"), dict):
        raise PaymentBoundaryError("Q1 V2 is required")
    return build_prepayment_summary(order, visitor)


def _email_scope_signature(scope_hash: str, legal_terms_version: str | None) -> str:
    value = f"{scope_hash}:{legal_terms_version or ''}:{SUMMARY_VERSION}"
    return hashlib.sha256(value.encode()).hexdigest()


def _require_current_email_attempt(order: Order, scope_hash: str) -> None:
    signature = _email_scope_signature(scope_hash, order.legal_terms_version)
    meta = (order.extended_brief or {}).get("payment_boundary_v2_email") or {}
    if meta.get("signature") != signature:
        raise PaymentBoundaryError("Pre-payment summary email is required for the current scope")
    state = meta.get("operation_state")
    if state == "sent" and meta.get("provider_attempted_at"):
        return
    if state == "failed" and meta.get("provider_attempted_at"):
        return
    raise PaymentBoundaryError("Pre-payment summary email operation has not completed for the current scope")


async def send_prepayment_summary(
    db: Session, visitor: SiteFormoVisitor, order_id: str,
    provider: Callable[[str, str, str], Awaitable[Any]],
) -> dict[str, Any]:
    project_binding(db, visitor, order_id)
    order = db.execute(select(Order).where(Order.id == order_id).with_for_update()).scalar_one_or_none()
    if order is None or order.status != OrderStatus.DRAFT:
        raise Q1OwnershipError("Only the current draft project may send a pre-payment summary")
    summary = build_prepayment_summary(order, visitor)
    confirmation = summary["confirmation_state"]
    if not confirmation["package_and_addons_confirmed"]:
        raise PaymentBoundaryError("Package and add-ons confirmation is required")
    if not confirmation["brief_confirmed_at"]:
        raise PaymentBoundaryError("Project Brief confirmation is required")
    approved = approved_legal_terms_version()
    if not confirmation["legal_confirmed_at"] or not approved:
        raise PaymentBoundaryError("Approved legal confirmation is required")
    if confirmation["legal_terms_version"] != approved:
        raise PaymentBoundaryError("Legal terms version is no longer current")
    recipient = getattr(order.client, "email", None)
    if not recipient:
        raise PaymentBoundaryError("A verified project email address is required", 409)
    signature = _email_scope_signature(summary["scope"]["scope_hash"], approved)
    extended = dict(order.extended_brief or {})
    meta = dict(extended.get("payment_boundary_v2_email") or {})
    if meta.get("signature") == signature and order.prepayment_summary_email_status == "sent":
        return {"order_id": order.id, "result": "already_sent", "email_status": "sent", "sent_at": order.prepayment_summary_email_sent_at, "retry_allowed": False}
    if meta.get("signature") == signature and meta.get("operation_state") == "sending":
        try:
            requested_at = _utc(datetime.fromisoformat(str(meta.get("requested_at"))))
        except (TypeError, ValueError):
            requested_at = None
        if requested_at and _now() - requested_at < timedelta(minutes=10):
            return {"order_id": order.id, "result": "in_progress", "email_status": "pending", "sent_at": None, "retry_allowed": False}
    meta = {"signature": signature, "operation_state": "sending", "requested_at": _now().isoformat()}
    extended["payment_boundary_v2_email"] = meta
    order.extended_brief = extended
    order.prepayment_summary_email_status = "pending"
    order.prepayment_summary_email_sent_at = None
    db.commit()
    message = compose_prepayment_summary_email(summary)
    try:
        await provider(recipient, message["subject"], message["html"])
    except Exception:
        db.rollback()
        order = db.execute(select(Order).where(Order.id == order_id).with_for_update()).scalar_one()
        extended = dict(order.extended_brief or {})
        meta = dict(extended.get("payment_boundary_v2_email") or {})
        if meta.get("signature") == signature:
            meta["operation_state"] = "failed"
            meta["provider_attempted_at"] = _now().isoformat()
            extended["payment_boundary_v2_email"] = meta
            order.extended_brief = extended
            order.prepayment_summary_email_status = "failed"
            order.prepayment_summary_email_sent_at = None
            db.commit()
        return {"order_id": order.id, "result": "failed", "email_status": "failed", "sent_at": None, "retry_allowed": True}
    order = db.execute(select(Order).where(Order.id == order_id).with_for_update()).scalar_one()
    extended = dict(order.extended_brief or {})
    meta = dict(extended.get("payment_boundary_v2_email") or {})
    current_summary = build_prepayment_summary(order, visitor)
    current_signature = _email_scope_signature(current_summary["scope"]["scope_hash"], approved)
    sent_at = _now()
    meta["operation_state"] = "sent" if current_signature == signature else "sent_for_previous_scope"
    meta["provider_attempted_at"] = sent_at.isoformat()
    meta["sent_at"] = sent_at.isoformat()
    extended["payment_boundary_v2_email"] = meta
    order.extended_brief = extended
    order.prepayment_summary_email_status = "sent" if current_signature == signature else "pending"
    order.prepayment_summary_email_sent_at = sent_at if current_signature == signature else None
    db.commit()
    if current_signature != signature:
        return {"order_id": order.id, "result": "failed", "email_status": "pending", "sent_at": None, "retry_allowed": True}
    return {"order_id": order.id, "result": "sent", "email_status": "sent", "sent_at": sent_at, "retry_allowed": False}


def build_payment_snapshot(order: Order, visitor: SiteFormoVisitor) -> tuple[dict[str, Any], str]:
    calculation = _scope_calculation(order, visitor, require_checkout_ready=True)
    q2 = calculation["q2"]
    qualification = calculation["qualification"]
    if not order.brief_confirmed_at:
        raise PaymentBoundaryError("Project Brief confirmation is required")
    if not order.legal_confirmed_at or not order.legal_terms_version:
        raise PaymentBoundaryError("Versioned legal confirmation is required")
    if order.legal_terms_version != approved_legal_terms_version():
        raise PaymentBoundaryError("Legal terms version is no longer current")
    _require_current_email_attempt(order, calculation["scope_hash"])
    snapshot = {
        "order_id": order.id,
        "siteformo_visitor_id": str(visitor.id),
        "q2_schema_version": q2["schema_version"],
        "q2_content_hash": calculation["q2_content_hash"],
        "scope_qualification_version": qualification["rule_version"],
        "scope_hash": calculation["scope_hash"],
        **{key: calculation[key] for key in (
            "base_package", "base_package_price_cents", "initial_deposit_rate",
            "initial_deposit_amount_cents", "currency", "confirmed_addons",
            "addons_due_now_cents", "remaining_base_package_balance_cents",
            "confirmed_addons_balance_cents", "current_final_balance_cents",
        )},
        "brief_confirmed_at": order.brief_confirmed_at.isoformat(),
        "legal_terms_version": order.legal_terms_version,
        "legal_confirmed_at": order.legal_confirmed_at.isoformat(),
        "scope_version": SCOPE_VERSION,
    }
    canonical = json.dumps(snapshot, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return snapshot, hashlib.sha256(canonical.encode()).hexdigest()


def create_checkout(
    db: Session,
    visitor: SiteFormoVisitor,
    order_id: str,
    stripe_create: Callable[..., Any],
) -> tuple[PaymentAttempt, bool]:
    project_binding(db, visitor, order_id)
    order = db.execute(select(Order).where(Order.id == order_id).with_for_update()).scalar_one_or_none()
    if order is None or order.status != OrderStatus.DRAFT:
        raise PaymentBoundaryError("Only the current draft project may enter checkout")
    if not isinstance((order.brief_answers or {}).get("q1_v2"), dict):
        raise PaymentBoundaryError("Q1 V2 is required")
    snapshot, digest = build_payment_snapshot(order, visitor)
    attempts = db.execute(select(PaymentAttempt).where(PaymentAttempt.order_id == order_id).order_by(PaymentAttempt.created_at.desc())).scalars().all()
    paid = next((item for item in attempts if item.status == "paid"), None)
    if paid:
        if paid.scope_snapshot_hash != digest:
            raise PaymentBoundaryError("A paid scope cannot be superseded")
        raise PaymentBoundaryError("Initial deposit is already paid")
    active = next((item for item in attempts if item.status in ACTIVE_ATTEMPT_STATUSES), None)
    now = _now()
    if active and active.expires_at and _utc(active.expires_at) <= now:
        active.status = "expired"
        active = None
        order.deposit_status = "cancelled"
    if active and active.scope_snapshot_hash == digest and active.stripe_checkout_session_id and active.checkout_url:
        return active, True
    if active:
        active.status = "superseded"
        active.superseded_at = now
    attempt = PaymentAttempt(
        order_id=order_id,
        siteformo_visitor_id=visitor.id,
        scope_snapshot_hash=digest,
        scope_version=SCOPE_VERSION,
        scope_snapshot=snapshot,
        base_package=snapshot["base_package"],
        base_package_price_cents=snapshot["base_package_price_cents"],
        currency=CURRENCY,
        expected_total_amount_cents=snapshot["initial_deposit_amount_cents"],
        expected_deposit_amount_cents=snapshot["initial_deposit_amount_cents"],
        confirmed_addons_snapshot=snapshot["confirmed_addons"],
        legal_terms_version=order.legal_terms_version,
        legal_confirmed_at=order.legal_confirmed_at,
        status="creating",
        expires_at=now + timedelta(hours=24),
    )
    db.add(attempt)
    db.flush()
    success = f"{os.getenv('APP_BASE_URL', 'https://ie.siteformo.com').rstrip('/')}/payment-success/?order_id={order_id}&session_id={{CHECKOUT_SESSION_ID}}"
    cancel = f"{os.getenv('APP_BASE_URL', 'https://ie.siteformo.com').rstrip('/')}/extended-questionnaire/?order_id={order_id}&payment=cancelled"
    try:
        session = stripe_create(
            mode="payment",
            payment_method_types=["card"],
            client_reference_id=order_id,
            line_items=[{"price_data": {"currency": "eur", "product_data": {"name": f"SiteFormo {attempt.base_package.title()} initial deposit"}, "unit_amount": attempt.expected_deposit_amount_cents}, "quantity": 1}],
            metadata={"payment_contract": "q2_v2_initial_deposit", "payment_attempt_id": attempt.id, "order_id": order_id, "scope_hash": digest},
            success_url=success,
            cancel_url=cancel,
            idempotency_key=f"siteformo-initial-{attempt.id}",
        )
    except Exception:
        attempt.status = "failed"
        order.deposit_status = "failed"
        db.commit()
        raise PaymentBoundaryError("Stripe Checkout could not be created", 502) from None
    attempt.stripe_checkout_session_id = str(getattr(session, "id", None) or session["id"])
    attempt.checkout_url = str(getattr(session, "url", None) or session["url"])
    expires = getattr(session, "expires_at", None)
    if expires:
        attempt.expires_at = datetime.fromtimestamp(int(expires), timezone.utc)
    attempt.status = "checkout_created"
    order.deposit_status = "checkout_created"
    order.deposit_amount_cents = attempt.expected_deposit_amount_cents
    order.deposit_currency = CURRENCY
    db.commit()
    db.refresh(attempt)
    return attempt, False


def payment_status(db: Session, visitor: SiteFormoVisitor, order_id: str) -> dict[str, Any]:
    project_binding(db, visitor, order_id)
    order = db.get(Order, order_id)
    if order is None:
        raise Q1OwnershipError("Project does not exist")
    status = order.deposit_status or "not_started"
    confirmed = status == "paid"
    if confirmed:
        q1_exists = isinstance((order.brief_answers or {}).get("q1_v2"), dict)
        q2_exists = isinstance((order.extended_brief or {}).get("q2_v2"), dict)
        downstream_started = has_post_design_activity(order)
        direction_required = (
            q1_exists
            and q2_exists
            and not order.design_direction
            and not downstream_started
        )
        interaction_required = (
            q1_exists
            and q2_exists
            and bool(order.design_direction)
            and interaction_preference_record(order) is None
            and not downstream_started
        )
        if direction_required:
            next_step = "design_direction"
        elif interaction_required:
            next_step = "interaction_preference"
        else:
            next_step = "post_payment_pending"
    elif status in {"checkout_created", "pending"}:
        next_step = "await_payment_confirmation"
    else:
        next_step = "complete_payment"
    return {"order_id": order_id, "deposit_status": status, "payment_confirmed": confirmed, "next_step": next_step, "retry_allowed": status in {"not_started", "failed", "cancelled"}}


def process_v2_checkout_completed(db: Session, event: Any) -> dict[str, Any]:
    event_id = str(event["id"])
    existing = db.execute(select(StripeWebhookEvent).where(StripeWebhookEvent.stripe_event_id == event_id)).scalar_one_or_none()
    if existing:
        return {"status": "duplicate", "processing_result": existing.processing_result}
    session = event["data"]["object"]
    session_id = str(session.get("id") or "")
    attempt = db.execute(select(PaymentAttempt).where(PaymentAttempt.stripe_checkout_session_id == session_id).with_for_update()).scalar_one_or_none()
    ledger = StripeWebhookEvent(stripe_event_id=event_id, event_type=str(event["type"]), payment_attempt_id=attempt.id if attempt else None, order_id=attempt.order_id if attempt else None)
    db.add(ledger)
    result = "rejected_unknown_session"
    if attempt:
        order = db.execute(select(Order).where(Order.id == attempt.order_id).with_for_update()).scalar_one_or_none()
        amount = int(session.get("amount_total") or -1)
        currency = str(session.get("currency") or "").upper()
        metadata = session.get("metadata") or {}
        current_hash = None
        if order:
            try:
                visitor = db.get(SiteFormoVisitor, attempt.siteformo_visitor_id)
                current_hash = build_payment_snapshot(order, visitor)[1] if visitor else None
            except Exception:
                current_hash = None
        if session.get("payment_status") != "paid": result = "rejected_not_paid"
        elif amount != attempt.expected_deposit_amount_cents: result = "rejected_amount_mismatch"
        elif currency != attempt.currency: result = "rejected_currency_mismatch"
        elif str(session.get("client_reference_id") or "") != attempt.order_id: result = "rejected_order_mismatch"
        elif metadata.get("payment_attempt_id") != attempt.id or metadata.get("scope_hash") != attempt.scope_snapshot_hash: result = "rejected_metadata_mismatch"
        elif current_hash != attempt.scope_snapshot_hash: result = "rejected_stale_scope"
        elif attempt.status == "superseded": result = "rejected_superseded"
        elif attempt.status == "paid": result = "already_paid"
        else:
            attempt.status = "paid"
            attempt.stripe_payment_intent_id = str(session.get("payment_intent") or "") or None
            attempt.completed_at = _now()
            order.deposit_status = "paid"
            order.deposit_paid_at = attempt.completed_at
            order.deposit_amount_cents = attempt.expected_deposit_amount_cents
            order.deposit_currency = CURRENCY
            result = "paid"
    ledger.processing_result = result
    ledger.processed_at = _now()
    db.commit()
    return {"status": "ok" if result in {"paid", "already_paid"} else "rejected", "processing_result": result}
