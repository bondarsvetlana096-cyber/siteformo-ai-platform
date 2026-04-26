import os
import stripe
import requests

from fastapi import APIRouter, Request, HTTPException


router = APIRouter()

stripe.api_key = os.getenv("STRIPE_SECRET_KEY")
endpoint_secret = os.getenv("STRIPE_WEBHOOK_SECRET")


def send_owner_payment_email(order_id, customer_email, tier, deposit_eur):
    resend_api_key = os.getenv("RESEND_API_KEY")
    email_from = os.getenv("EMAIL_FROM", "SiteFormo <hello@siteformo.com>")
    owner_email = os.getenv("OWNER_EMAIL", "klon97048@gmail.com")

    if not resend_api_key:
        print("⚠️ RESEND_API_KEY is missing. Owner email not sent.")
        return

    subject = f"💰 New SiteFormo payment received — €{deposit_eur}"

    body = f"""
New SiteFormo payment received.

Payment details:

Package: {tier or "Unknown"}
Deposit paid: €{deposit_eur or "Unknown"}
Order ID: {order_id or "Not provided"}
Customer email: {customer_email or "Not provided"}

Next steps:
1. Review the order.
2. Contact the client.
3. Send the detailed project questionnaire.
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


@router.post("/api/payments/webhook")
async def stripe_webhook(request: Request):
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

        order_id = session["client_reference_id"] if "client_reference_id" in session else None
        customer_email = session["customer_email"] if "customer_email" in session else None

        metadata = session["metadata"] if "metadata" in session else {}

        tier = metadata["tier"] if "tier" in metadata else ""
        deposit_eur = metadata["deposit_eur"] if "deposit_eur" in metadata else ""

        print("🔥 PAYMENT SUCCESS")
        print("Order ID:", order_id)
        print("Email:", customer_email)
        print("Tier:", tier)
        print("Deposit EUR:", deposit_eur)

        send_owner_payment_email(
            order_id=order_id,
            customer_email=customer_email,
            tier=tier,
            deposit_eur=deposit_eur,
        )

    return {"status": "ok"}