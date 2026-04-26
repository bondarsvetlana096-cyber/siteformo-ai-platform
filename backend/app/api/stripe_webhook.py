import os
import inspect
import base64
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

APP_BASE_URL = os.getenv("APP_BASE_URL", "https://siteformo.com")
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


def _questionnaire_link(order_id: Optional[str]) -> str:
    return f"{APP_BASE_URL}/extended-questionnaire?order_id={order_id or ''}"


def _send_resend_email(to_email: str, subject: str, body: str):
    resend_api_key = os.getenv("RESEND_API_KEY")
    email_from = os.getenv("EMAIL_FROM", "SiteFormo <hello@siteformo.com>")

    if not resend_api_key:
        print("⚠️ RESEND_API_KEY is missing. Email not sent.")
        return

    if not to_email:
        print("⚠️ Recipient email is missing. Email not sent.")
        return

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

    if response.status_code >= 400:
        print("❌ Failed to send email:", response.status_code, response.text)
    else:
        print("✅ Email sent:", subject)


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
    references = (
        brief_answers.get("references")
        or getattr(order, "reference_site_notes", None)
        or "Not provided"
    )

    source_url = contact["source_url"] or "Not provided"
    business_description = contact["business_description"] or "Not provided"
    questionnaire_link = _questionnaire_link(order_id)

    subject = f"💰 Оплата в размере {deposit_eur} евро произведена"

    body = f"""
Оплата в размере {deposit_eur or "Unknown"} евро произведена.

Stripe payment received.
Order status: APPROVED.

ВАЖНО:
Stripe только разблокирует расширенную анкету.
Генерация сайта НЕ запускается после оплаты.
Генерация запускается только после POST /extended-brief.

Payment details:

Package: {tier or "Unknown"}
Deposit paid: €{deposit_eur or "Unknown"}
Order ID: {order_id or "Not provided"}
Order status: APPROVED

Client contact from first quiz:

Email: {client_email}
WhatsApp / phone: {client_phone}
Telegram: {client_telegram}

Project details from first quiz:

Existing website: {source_url}
Business / niche: {business_description}
Goal: {goal}
Style: {style}
Urgency: {urgency}
Feature: {feature}
Scope: {scope}
References: {references}

Client questionnaire link:

{questionnaire_link}

Next step:
Client must complete the extended questionnaire.
Email and phone are required in the extended questionnaire.
Only after that generation_service.run(order) will be called.
"""

    _send_resend_email(
        to_email=OWNER_EMAIL,
        subject=subject,
        body=body,
    )


def send_client_payment_email(customer_email, order_id, tier, deposit_eur):
    questionnaire_link = _questionnaire_link(order_id)

    subject = "✅ Payment received — complete your SiteFormo project brief"

    body = f"""
Thank you for your payment.

We have received your SiteFormo deposit.

Payment details:

Package: {tier or "Unknown"}
Deposit paid: €{deposit_eur or "Unknown"}
Order ID: {order_id or "Not provided"}

Important next step:

Please complete your extended project questionnaire here:

{questionnaire_link}

Required before generation:
- Email address
- Phone / WhatsApp number

We will start generating your website only after you complete this questionnaire.

SiteFormo
"""

    _send_resend_email(
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
        payment_line = f"Оплата в размере {deposit_eur} евро произведена."
        deposit_line = f"€{deposit_eur}"
    else:
        payment_line = "Owner bypass: оплата не требовалась."
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
        raise HTTPException(status_code=400, detail=str(e))

    if event["type"] == "checkout.session.completed":
        session = event["data"]["object"]

        order_id = _safe_get(session, "client_reference_id")
        customer_email = _safe_get(session, "customer_email")

        metadata = _safe_get(session, "metadata", {}) or {}

        tier = _safe_get(metadata, "tier", "")
        deposit_eur = _safe_get(metadata, "deposit_eur", "")

        order = _load_order(db, order_id)
        contact = _extract_order_contact(order)

        if not customer_email and contact["client_email"]:
            customer_email = contact["client_email"]

        if order:
            _set_if_exists(order, "status", "APPROVED")
            _set_if_exists(order, "payment_status", "PAID")
            _set_if_exists(order, "deposit_paid", True)
            _set_if_exists(order, "deposit_eur", deposit_eur)
            _set_if_exists(order, "tier", tier)

            db.add(order)
            db.commit()
            db.refresh(order)
        else:
            print("⚠️ Order not found for Stripe session:", order_id)

        print("🔥 PAYMENT SUCCESS")
        print("Order ID:", order_id)
        print("Status:", "APPROVED")
        print("Email:", customer_email)
        print("Phone:", contact["client_phone"])
        print("Telegram:", contact["client_telegram"])
        print("Tier:", tier)
        print("Deposit EUR:", deposit_eur)

        send_owner_payment_email(
            order_id=order_id,
            customer_email=customer_email,
            tier=tier,
            deposit_eur=deposit_eur,
            order=order,
        )

        send_client_payment_email(
            customer_email=customer_email,
            order_id=order_id,
            tier=tier,
            deposit_eur=deposit_eur,
        )

    return {"status": "ok"}


@router.post("/extended-brief")
async def submit_extended_brief(
    payload: ExtendedBriefPayload,
    db: Session = Depends(get_db),
):
    order = _load_order(db, payload.order_id)

    if not order:
        raise HTTPException(status_code=404, detail="Order not found")

    current_status = getattr(order, "status", None)

    if current_status != "APPROVED":
        raise HTTPException(
            status_code=400,
            detail="Order is not approved. Payment or owner approval is required first.",
        )

    email = (payload.answers.get("email") or "").strip()
    phone = (payload.answers.get("phone") or "").strip()

    if not email or not phone:
        raise HTTPException(
            status_code=400,
            detail="Email and phone are required before generation.",
        )

    _set_if_exists(order, "email", email)
    _set_if_exists(order, "phone", phone)
    _set_if_exists(order, "extended_brief_answers", payload.answers)
    _set_if_exists(order, "extended_brief", payload.answers)
    _set_if_exists(order, "brief_answers_extended", payload.answers)
    _set_if_exists(order, "status", "GENERATING")

    db.add(order)
    db.commit()
    db.refresh(order)

    try:
        generation_result = generation_service.run(order)

        if inspect.isawaitable(generation_result):
            generation_result = await generation_result

        initial_answers = getattr(order, "brief_answers", None) or {}
        extended_answers = payload.answers

        pdf_path = create_divi_pdf(
            order=order,
            initial_answers=initial_answers,
            extended_answers=extended_answers,
            generation_result=generation_result,
        )

        _set_if_exists(order, "status", "GENERATED")
        _set_if_exists(order, "generation_result", generation_result)
        _set_if_exists(order, "generated_site", generation_result)

        db.add(order)
        db.commit()
        db.refresh(order)

        send_owner_generation_result_email_with_pdf(
            order=order,
            initial_answers=initial_answers,
            extended_answers=extended_answers,
            generation_result=generation_result,
            pdf_path=pdf_path,
        )

        return {
            "status": "ok",
            "message": "Extended brief submitted. Site generation completed.",
            "order_id": payload.order_id,
        }

    except Exception as e:
        _set_if_exists(order, "status", "GENERATION_FAILED")
        _set_if_exists(order, "generation_error", str(e))

        db.add(order)
        db.commit()

        print("❌ Generation failed:", str(e))

        raise HTTPException(
            status_code=500,
            detail=f"Generation failed: {str(e)}",
        )