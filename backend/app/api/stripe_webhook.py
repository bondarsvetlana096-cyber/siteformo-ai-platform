import stripe
import os
from fastapi import APIRouter, Request

router = APIRouter()

stripe.api_key = os.getenv("STRIPE_SECRET_KEY")
endpoint_secret = os.getenv("STRIPE_WEBHOOK_SECRET")


@router.post("/api/payments/webhook")
async def stripe_webhook(request: Request):
    payload = await request.body()
    sig_header = request.headers.get("stripe-signature")

    try:
        event = stripe.Webhook.construct_event(
            payload, sig_header, endpoint_secret
        )
    except Exception as e:
        return {"error": str(e)}

    if event["type"] == "checkout.session.completed":
        session = event["data"]["object"]

        print("🔥 PAYMENT SUCCESS")
        print("Order ID:", session.get("client_reference_id"))
        print("Email:", session.get("customer_email"))

        # тут позже:
        # → отправка email
        # → создание проекта
        # → уведомление тебе

    return {"status": "ok"}