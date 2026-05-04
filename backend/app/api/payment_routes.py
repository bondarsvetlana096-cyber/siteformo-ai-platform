import os
import uuid
import stripe

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

router = APIRouter(prefix="/api/payments", tags=["payments"])

stripe.api_key = os.getenv("STRIPE_SECRET_KEY")

APP_BASE_URL = os.getenv("APP_BASE_URL", "https://ie.siteformo.com")


# ✅ WHITELIST (бот и тесты)
WHITELIST_EMAILS = {
    "klon97048@gmail.com",
    "porto3011969@gmail.com"
}

WHITELIST_TELEGRAM = {
    "@mironkasper"
}

WHITELIST_WHATSAPP = {
    # добавишь позже если нужно
}


class CheckoutRequest(BaseModel):
    amount: int = Field(..., ge=1)
    order_id: str | None = None
    tier: str | None = None
    package_name: str | None = None
    package_range: str | None = None
    market: str | None = None
    customer_email: str | None = None
    customer_phone: str | None = None
    customer_telegram: str | None = None

    success_url: str | None = None
    cancel_url: str | None = None


def is_whitelisted(data: CheckoutRequest) -> bool:
    email = (data.customer_email or "").lower().strip()
    phone = (data.customer_phone or "").strip()
    telegram = (data.customer_telegram or "").strip()

    if email in WHITELIST_EMAILS:
        return True

    if telegram in WHITELIST_TELEGRAM:
        return True

    if phone in WHITELIST_WHATSAPP:
        return True

    return False


@router.post("/create-checkout")
async def create_checkout(data: CheckoutRequest):
    if not stripe.api_key:
        raise HTTPException(status_code=500, detail="Stripe not configured")

    order_id = data.order_id or str(uuid.uuid4())

    success_url = (
        data.success_url
        or f"{APP_BASE_URL}/payment-success?session_id={{CHECKOUT_SESSION_ID}}&order_id={order_id}"
    )

    cancel_url = (
        data.cancel_url
        or f"{APP_BASE_URL}/?payment=cancel"
    )

    # 🔥 ВАЖНО: BYPASS ДЛЯ БОТА / ТЕСТОВ
    if is_whitelisted(data):
        return {
            "status": "bypass",
            "url": f"{APP_BASE_URL}/payment-success?bypass=1&order_id={order_id}",
            "order_id": order_id
        }

    # обычный Stripe
    try:
        session = stripe.checkout.Session.create(
            mode="payment",
            payment_method_types=["card"],
            customer_email=data.customer_email or None,
            client_reference_id=order_id,
            line_items=[
                {
                    "price_data": {
                        "currency": "eur",
                        "product_data": {
                            "name": data.package_name or "Website deposit",
                        },
                        "unit_amount": data.amount * 100,
                    },
                    "quantity": 1,
                }
            ],
            metadata={
                "order_id": order_id,
                "tier": data.tier or "",
                "package_name": data.package_name or "",
            },
            success_url=success_url,
            cancel_url=cancel_url,
        )

        return {
            "url": session.url,
            "order_id": order_id
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))