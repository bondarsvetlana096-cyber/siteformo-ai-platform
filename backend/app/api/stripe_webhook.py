import os
import stripe

from fastapi import APIRouter, Request, HTTPException


router = APIRouter()

stripe.api_key = os.getenv("STRIPE_SECRET_KEY")
endpoint_secret = os.getenv("STRIPE_WEBHOOK_SECRET")


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

        order_id = getattr(session, "client_reference_id", None)
        customer_email = getattr(session, "customer_email", None)

        metadata = getattr(session, "metadata", {}) or {}
        tier = metadata.get("tier", "")
        deposit_eur = metadata.get("deposit_eur", "")

        print("🔥 PAYMENT SUCCESS")
        print("Order ID:", order_id)
        print("Email:", customer_email)
        print("Tier:", tier)
        print("Deposit EUR:", deposit_eur)

    return {"status": "ok"}