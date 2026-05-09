import os
import inspect
import base64
import re
import stripe
import requests

from typing import Any, Dict, Optional

from fastapi import APIRouter, Request, HTTPException, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session, joinedload

from app.db.session import get_db
from app.models.order import Order
from app.services import generation_service
from app.services.pdf_service import create_divi_pdf


router = APIRouter()

stripe.api_key = os.getenv("STRIPE_SECRET_KEY")
endpoint_secret = os.getenv("STRIPE_WEBHOOK_SECRET")

APP_BASE_URL = os.getenv("APP_BASE_URL", "https://ie.siteformo.com")
OWNER_EMAIL = os.getenv("OWNER_EMAIL", "klon97048@gmail.com")


def _safe_get(obj, key, default=None):
    if obj is None:
        return default

    try:
        if key in obj:
            return obj[key]
    except Exception:
        pass

    return getattr(obj, key, default)


def _set_if_exists(obj, field: str, value: Any) -> bool:
    if hasattr(obj, field):
        setattr(obj, field, value)
        return True
    return False


def _load_order(db: Session, order_id: Optional[str]):
    if not order_id:
        return None

    return (
        db.query(Order)
        .options(joinedload(Order.client))
        .filter(Order.id == order_id)
        .first()
    )


def _extract_order_contact(order):
    if not order:
        return {
            "client_email": None,
            "client_phone": None,
            "client_telegram": None,
            "source_url": None,
            "business_description": None,
            "brief_answers": {},
        }

    client = getattr(order, "client", None)
    brief_answers = getattr(order, "brief_answers", None) or {}

    client_email = (
        getattr(client, "email", None)
        or getattr(order, "email", None)
        or brief_answers.get("email")
    )

    client_phone = (
        getattr(client, "phone", None)
        or getattr(client, "whatsapp", None)
        or getattr(order, "phone", None)
        or brief_answers.get("phone")
        or brief_answers.get("whatsapp")
    )

    client_telegram = (
        getattr(client, "telegram_handle", None)
        or getattr(client, "telegram", None)
        or getattr(order, "telegram_handle", None)
        or brief_answers.get("telegram_handle")
        or brief_answers.get("telegram")
    )

    return {
        "client_email": client_email,
        "client_phone": client_phone,
        "client_telegram": client_telegram,
        "source_url": getattr(order, "source_url", None),
        "business_description": getattr(order, "desired_site_description", None),
        "brief_answers": brief_answers,
    }


def _strip_links(value: Any) -> str:
    """Remove URLs from email text so emails do not contain clickable links."""
    text = str(value or "")
    text = re.sub(r"https?://\S+", "[link removed]", text, flags=re.IGNORECASE)
    text = re.sub(r"www\.\S+", "[link removed]", text, flags=re.IGNORECASE)
    return text


def _format_eur(value: Any) -> str:
    if value is None or value == "":
        return "Unknown"

    try:
        number = float(value)
        if number.is_integer():
            return f"€{int(number)}"
        return f"€{number:.2f}"
    except Exception:
        return f"€{value}"


def _send_resend_email(to_email: str, subject: str, body: str):
    resend_api_key = os.getenv("RESEND_API_KEY")
    email_from = os.getenv("EMAIL_FROM", "SiteFormo <hello@siteformo.com>")

    if not resend_api_key:
        print("⚠️ RESEND_API_KEY is missing. Email not sent.")
        return False

    if not to_email:
        print("⚠️ Recipient email is missing. Email not sent.")
        return False

    body = _strip_links(body)

    try:
        response = requests.post(
            "https://api.resend.com/emails",
            headers={
                "Authorization": f"Bearer {resend_api_key}",
                "Content-Type": "application/json",
            },
            json={
                "from": email_from,
                "to": [to_email],
                "subject": subject,
                "text": body,
            },
            timeout=15,
        )
    except Exception as e:
        print("❌ Resend request failed:", str(e))
        return False

    if response.status_code >= 400:
        print("❌ Failed to send email:", response.status_code, response.text)
        return False

    print("✅ Email sent:", subject, "to", to_email)
    return True


def send_owner_payment_email(order_id, customer_email, tier, deposit_eur, order=None):
    contact = _extract_order_contact(order)
    brief_answers = contact["brief_answers"]

    client_email = customer_email or contact["client_email"] or "Not provided"
    client_phone = contact["client_phone"] or "Not provided"
    client_telegram = contact["client_telegram"] or "Not provided"

    goal = brief_answers.get("goal") or brief_answers.get("main_goal") or "Not provided"
    style = brief_answers.get("style") or "Not provided"
    urgency = brief_answers.get("urgency") or "Not provided"
    feature = brief_answers.get("feature") or "Not provided"
    scope = brief_answers.get("scope") or "Not provided"

    deposit_line = _format_eur(deposit_eur)
    business_description = contact["business_description"] or "Not provided"
    subject = f"💰 SiteFormo payment received — {deposit_line}"

    body = f"""
Payment received ✅

Stripe payment received.
Order status: APPROVED.

IMPORTANT:
Payment was completed through Stripe.
The development team should review the confirmed brief and continue the order flow.
Do not include links in emails.

Payment details:

Package: {tier or "Unknown"}
Deposit paid: {deposit_line}
Order ID: {order_id or "Not provided"}
Order status: APPROVED

Client contact from first quiz:

Email: {client_email}
WhatsApp / phone: {client_phone}
Telegram: {client_telegram}

Project details from first quiz:

Existing website: provided in order record, not included in email for safety
Business / niche: {business_description}
Goal: {goal}
Style: {style}
Urgency: {urgency}
Feature: {feature}
Scope: {scope}
References: provided in order record, not included in email for safety

Next step:
Check the order in the SiteFormo admin/backend records and continue the design-preview flow.

Important flow:
1. Stripe deposit confirms the order deposit.
2. The client should continue on the website after payment.
3. Design previews and logo options, if ordered, are prepared after project review.
4. No links are included in this email by design.
"""

    return _send_resend_email(
        to_email=OWNER_EMAIL,
        subject=subject,
        body=body,
    )


def send_client_payment_email(customer_email, order_id, tier, deposit_eur):
    subject = "✅ Your SiteFormo project has entered production"

    deposit_line = _format_eur(deposit_eur)
    tier_lower = str(tier or "").lower().strip()

    if tier_lower == "starter":
        revision_text = """
• your package includes 1 revision round
• revisions are intended for refinements, not complete redesigns
"""
    else:
        revision_text = """
• your package includes 2 revision rounds
• one revision round means one grouped list of changes submitted together
• revisions are intended for refinements, not complete redesigns
• changing the selected design direction or rebuilding the website from scratch is not included in revisions
"""

    body = f"""
Production started successfully.

Your project has been added to the SiteFormo production pipeline.

Our production team is now preparing your website structure, visual direction and homepage presentation based on the selections you made earlier.

Payment details:

Package: {tier or "Unknown"}
Deposit paid: {deposit_line}
Order ID: {order_id or "Not provided"}

What happens next?

1. Your website enters the production phase.

2. When your website preview is ready, you will receive a private review access message by email.

3. You will be able to review your completed website and check pages and interactions.

4. You will submit requested changes through a simple revision form.

5. After your final approval, your final ZIP package will be prepared for delivery.

Please note:

{revision_text}
• final ZIP/source files are delivered only after revision completion and final approval
• please include all requested changes together whenever possible

You may request cancellation within 1 hour after payment confirmation.
If production work has not significantly started yet,
your deposit will be refunded within 24 hours.

Thank you for choosing SiteFormo.
"""

    return _send_resend_email(
        to_email=customer_email,
        subject=subject,
        body=body,
    )

def send_owner_generation_result_email_with_pdf(
    order,
    initial_answers,
    extended_answers,
    generation_result,
    pdf_path,
):
    resend_api_key = os.getenv("RESEND_API_KEY")
    email_from = os.getenv("EMAIL_FROM", "SiteFormo <hello@siteformo.com>")
    owner_email = os.getenv("OWNER_EMAIL", "klon97048@gmail.com")

    if not resend_api_key:
        print("⚠️ RESEND_API_KEY is missing. Final owner email not sent.")
        return

    order_id = getattr(order, "id", "Unknown")
    deposit_eur = getattr(order, "deposit_eur", None)
    tier = getattr(order, "tier", "Unknown")

    email = extended_answers.get("email") or getattr(order, "email", "Not provided")
    phone = extended_answers.get("phone") or getattr(order, "phone", "Not provided")

    if deposit_eur:
        payment_line = f"Deposit payment received: {_format_eur(deposit_eur)}."
        deposit_line = _format_eur(deposit_eur)
    else:
        payment_line = "Owner bypass: payment was not required."
        deposit_line = "Owner bypass / no payment"

    subject = f"✅ SiteFormo generated — Order {order_id}"

    body = f"""
{payment_line}

Website generation completed.

Order details:
Order ID: {order_id}
Package: {tier}
Deposit paid: {deposit_line}
Status: GENERATED

Required contact from extended questionnaire:
Email: {email}
Phone / WhatsApp: {phone}

Initial quiz answers:
{initial_answers}

Extended questionnaire answers:
{extended_answers}

Generation result:
{generation_result}

Attached:
PDF file with Divi 5 ready website content.
"""

    body = _strip_links(body)

    with open(pdf_path, "rb") as f:
        pdf_base64 = base64.b64encode(f.read()).decode("utf-8")

    response = requests.post(
        "https://api.resend.com/emails",
        headers={
            "Authorization": f"Bearer {resend_api_key}",
            "Content-Type": "application/json",
        },
        json={
            "from": email_from,
            "to": [owner_email],
            "subject": subject,
            "text": body,
            "attachments": [
                {
                    "filename": f"SiteFormo_Divi5_Order_{order_id}.pdf",
                    "content": pdf_base64,
                }
            ],
        },
        timeout=20,
    )

    if response.status_code >= 400:
        print("❌ Failed to send final owner email:", response.status_code, response.text)
    else:
        print("✅ Final owner email with PDF sent")


class ExtendedBriefPayload(BaseModel):
    order_id: str
    answers: Dict[str, Any]


@router.post("/api/payments/webhook")
async def stripe_webhook(
    request: Request,
    db: Session = Depends(get_db),
):
    payload = await request.body()
    sig_header = request.headers.get("stripe-signature")

    try:
        event = stripe.Webhook.construct_event(
            payload=payload,
            sig_header=sig_header,
            secret=endpoint_secret,
        )
    except Exception as e:
        print("❌ Stripe webhook verification failed:", str(e))
        raise HTTPException(status_code=400, detail=str(e))

    if event["type"] == "checkout.session.completed":
        session = event["data"]["object"]

        amount_total = _safe_get(session, "amount_total", 0) or 0
        amount_eur = int(amount_total / 100) if amount_total else None

        metadata = _safe_get(session, "metadata", {}) or {}

        order_id = (
            _safe_get(metadata, "order_id")
            or _safe_get(session, "client_reference_id")
        )

        customer_email = (
            _safe_get(session, "customer_email")
            or _safe_get(_safe_get(session, "customer_details", {}) or {}, "email")
        )

        tier = _safe_get(metadata, "tier", "") or _safe_get(metadata, "package_name", "")
        deposit_eur = _safe_get(metadata, "deposit_eur") or amount_eur

        print("🔥 STRIPE CHECKOUT COMPLETED")
        print("Metadata:", metadata)
        print("Order ID:", order_id)
        print("Customer email:", customer_email)
        print("Tier:", tier)
        print("Deposit EUR:", deposit_eur)

        payment_type = _safe_get(metadata, "type", "deposit") or "deposit"

        if not order_id:
            print("❌ No order_id found in Stripe metadata or client_reference_id")
            return {
                "status": "error",
                "reason": "missing_order_id",
            }

        order = _load_order(db, order_id)

        if not order:
            print("⚠️ Order not found in database, but payment is real. Sending payment emails from Stripe metadata:", order_id)

            try:
                owner_sent = send_owner_payment_email(
                    order_id=order_id,
                    customer_email=customer_email,
                    tier=tier,
                    deposit_eur=deposit_eur,
                    order=None,
                )
                print("Owner payment email sent without DB order:", owner_sent)

                client_sent = False
                if customer_email:
                    client_sent = send_client_payment_email(
                        customer_email=customer_email,
                        order_id=order_id,
                        tier=tier,
                        deposit_eur=deposit_eur,
                    )
                    print("Client payment email sent without DB order:", client_sent)
                else:
                    print("⚠️ Client email missing in Stripe session. Client email not sent.")

                return {
                    "status": "ok",
                    "warning": "order_not_found_email_fallback_used",
                    "order_id": order_id,
                    "owner_email_sent": owner_sent,
                    "client_email_sent": client_sent,
                }
            except Exception as e:
                print("❌ Payment received but fallback emails failed:", str(e))
                return {
                    "status": "ok",
                    "warning": "order_not_found_and_fallback_email_failed",
                    "order_id": order_id,
                    "error": str(e),
                }

        contact = _extract_order_contact(order)

        if not customer_email and contact["client_email"]:
            customer_email = contact["client_email"]

        try:
            if payment_type == "final_payment":
                _set_if_exists(order, "status", "FINAL_APPROVED")
                _set_if_exists(order, "payment_status", "FINAL_PAID")
                _set_if_exists(order, "final_payment_paid", True)
                _set_if_exists(order, "final_paid_at", __import__("datetime").datetime.datetime.utcnow())
            else:
                _set_if_exists(order, "status", "APPROVED")
                _set_if_exists(order, "payment_status", "PAID")
                _set_if_exists(order, "deposit_paid", True)
                _set_if_exists(order, "deposit_eur", deposit_eur)
                _set_if_exists(order, "tier", tier)

            db.add(order)
            db.commit()
            db.refresh(order)

            print("✅ ORDER UPDATED")
            print("Order ID:", order.id)
            print("Status:", getattr(order, "status", None))
            print("Payment status:", getattr(order, "payment_status", None))

        except Exception as e:
            db.rollback()
            print("❌ Failed to update order:", str(e))
            raise HTTPException(status_code=500, detail="Failed to update order")

        if payment_type == "final_payment":
            print("✅ Final payment received. Final delivery still requires final approval if not already recorded.")
            return {"status": "ok", "type": "final_payment", "order_id": order_id}

        try:
            owner_sent = send_owner_payment_email(
                order_id=order_id,
                customer_email=customer_email,
                tier=tier,
                deposit_eur=deposit_eur,
                order=order,
            )
            print("Owner payment email sent:", owner_sent)

            if customer_email:
                client_sent = send_client_payment_email(
                    customer_email=customer_email,
                    order_id=order_id,
                    tier=tier,
                    deposit_eur=deposit_eur,
                )
                print("Client payment email sent:", client_sent)
            else:
                print("⚠️ Client email missing. Client email not sent.")

        except Exception as e:
            print("⚠️ Payment email failed, but order is already approved:", str(e))

    return {"status": "ok"}


@router.post("/api/orders/extended-brief-legacy-disabled")
async def submit_extended_brief_legacy_disabled(
    payload: ExtendedBriefPayload,
    db: Session = Depends(get_db),
):
    raise HTTPException(
        status_code=410,
        detail=(
            "This legacy endpoint is disabled. Use /api/orders/extended-brief; "
            "the correct flow is payment -> questionnaire -> design previews -> design selection -> full generation."
        ),
    )
