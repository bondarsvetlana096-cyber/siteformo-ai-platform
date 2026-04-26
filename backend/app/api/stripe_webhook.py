import os
import stripe
import requests

from fastapi import APIRouter, Request, HTTPException, Depends
from sqlalchemy.orm import Session, joinedload

from app.db.session import get_db
from app.models.order import Order


router = APIRouter()

stripe.api_key = os.getenv("STRIPE_SECRET_KEY")
endpoint_secret = os.getenv("STRIPE_WEBHOOK_SECRET")


def _safe_get(obj, key, default=None):
    if obj is None:
        return default

    try:
        if key in obj:
            return obj[key]
    except Exception:
        pass

    return getattr(obj, key, default)


def _load_order(db: Session, order_id: str | None):
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


def send_owner_payment_email(
    order_id,
    customer_email,
    tier,
    deposit_eur,
    order=None,
):
    resend_api_key = os.getenv("RESEND_API_KEY")
    email_from = os.getenv("EMAIL_FROM", "SiteFormo <hello@siteformo.com>")
    owner_email = os.getenv("OWNER_EMAIL", "klon97048@gmail.com")

    if not resend_api_key:
        print("⚠️ RESEND_API_KEY is missing. Owner email not sent.")
        return

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
    references = brief_answers.get("references") or getattr(order, "reference_site_notes", None) or "Not provided"

    source_url = contact["source_url"] or "Not provided"
    business_description = contact["business_description"] or "Not provided"

    subject = f"💰 New SiteFormo payment received — €{deposit_eur}"

    body = f"""
New SiteFormo payment received.

Payment details:

Package: {tier or "Unknown"}
Deposit paid: €{deposit_eur or "Unknown"}
Order ID: {order_id or "Not provided"}

Client contact:

Email: {client_email}
WhatsApp / phone: {client_phone}
Telegram: {client_telegram}

Project details:

Existing website: {source_url}
Business / niche: {business_description}
Goal: {goal}
Style: {style}
Urgency: {urgency}
Feature: {feature}
Scope: {scope}
References: {references}

Next steps:
1. Review the order.
2. Contact the client using the available contact method.
3. Make sure the client receives the detailed project questionnaire.
"""

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
        },
        timeout=15,
    )

    if response.status_code >= 400:
        print("❌ Failed to send owner email:", response.status_code, response.text)
    else:
        print("✅ Owner payment email sent")


def send_client_payment_email(customer_email, order_id, tier, deposit_eur):
    resend_api_key = os.getenv("RESEND_API_KEY")
    email_from = os.getenv("EMAIL_FROM", "SiteFormo <hello@siteformo.com>")

    if not customer_email:
        print("⚠️ Customer email is missing. Client confirmation email not sent.")
        return

    if not resend_api_key:
        print("⚠️ RESEND_API_KEY is missing. Client email not sent.")
        return

    confirm_link = f"https://siteformo.com/start-project?order_id={order_id or ''}"

    subject = "✅ Payment received — confirm your SiteFormo project"

    body = f"""
Thank you for your payment.

We have received your SiteFormo deposit.

Payment details:

Package: {tier or "Unknown"}
Deposit paid: €{deposit_eur or "Unknown"}
Order ID: {order_id or "Not provided"}

Important next step:

Please confirm and start your project using this link:

{confirm_link}

After confirmation, you will receive an additional questionnaire so we can better understand your business, design preferences, website structure, content, and exact project requirements.

Privacy notice:
We use your information only for your website project and internal communication. We do not sell, share, or transfer your personal data to third parties.

Refund policy reminder:
- 100% refund if you cancel within 1 hour after payment.
- 75% refund if you cancel within 24 hours after payment.
- No refund if you cancel more than 24 hours after payment.

If you have any questions, just reply to this email.

SiteFormo
"""

    response = requests.post(
        "https://api.resend.com/emails",
        headers={
            "Authorization": f"Bearer {resend_api_key}",
            "Content-Type": "application/json",
        },
        json={
            "from": email_from,
            "to": [customer_email],
            "subject": subject,
            "text": body,
        },
        timeout=15,
    )

    if response.status_code >= 400:
        print("❌ Failed to send client email:", response.status_code, response.text)
    else:
        print("✅ Client confirmation email sent")


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

        print("🔥 PAYMENT SUCCESS")
        print("Order ID:", order_id)
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
