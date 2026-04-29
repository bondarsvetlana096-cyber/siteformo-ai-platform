import os
import stripe
import uuid

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.db import database  # ВАЖНО: если ошибка — скажешь, поправлю

router = APIRouter(prefix="/api/payments", tags=["payments"])

stripe.api_key = os.getenv("STRIPE_SECRET_KEY")

OWNER_EMAIL = os.getenv("OWNER_EMAIL", "klon97048@gmail.com")
APP_BASE_URL = os.getenv("APP_BASE_URL", "https://siteformo.com")


class CheckoutRequest(BaseModel):
    amount: int = Field(..., ge=1)
    order_id: str | None = None
    tier: str | None = None
    package_name: str | None = None
    package_range: str | None = None
    market: str | None = None
    customer_email: str | None = None


def _is_owner_email(email: str | None) -> bool:
    return (email or "").strip().lower() == OWNER_EMAIL.strip().lower()


def _safe_deposit(amount: int) -> int:
    allowed_deposits = [475, 750, 1400, 2250]

    if amount in allowed_deposits:
        return amount

    raise HTTPException(
        status_code=400,
        detail="Invalid deposit amount.",
    )


@router.post("/create-checkout")
async def create_checkout(data: CheckoutRequest):
    if not stripe.api_key:
        raise HTTPException(status_code=500, detail="Stripe is not configured.")

    deposit = _safe_deposit(data.amount)

    # 🔥 1. Создаём order_id, если его нет
    if not data.order_id:
        order_id = str(uuid.uuid4())
    else:
        order_id = data.order_id.strip()

    # 🔥 2. СОЗДАЁМ ЗАКАЗ В БАЗЕ (ключевой момент)
    try:
        await database.execute(
            """
            INSERT INTO orders (id, email, plan, status, amount, deposit)
            VALUES (:id, :email, :plan, 'PENDING_PAYMENT', :amount, :deposit)
            """,
            {
                "id": order_id,
                "email": data.customer_email,
                "plan": data.tier,
                "amount": data.amount,
                "deposit": deposit,
            }
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"DB error: {str(e)}")

    # 🔥 OWNER BYPASS
    if _is_owner_email(data.customer_email):
        return {
            "status": "owner_bypass",
            "order_id": order_id,
            "questionnaire_url": f"{APP_BASE_URL}/extended-questionnaire/?order_id={order_id}",
            "deposit": deposit,
        }

    success_url = f"{APP_BASE_URL}/extended-questionnaire/?order_id={order_id}"
    cancel_url = os.getenv("FRONTEND_CANCEL_URL", f"{APP_BASE_URL}/?payment=cancel")

    product_name = data.package_name or f"SiteFormo {data.tier or 'Website'} deposit"

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
                            "name": product_name,
                            "description": "50% project deposit",
                        },
                        "unit_amount": deposit * 100,
                    },
                    "quantity": 1,
                }
            ],
            metadata={
                "order_id": order_id,
                "tier": data.tier or "",
                "package_name": data.package_name or "",
                "package_range": data.package_range or "",
                "market": data.market or "",
                "deposit_eur": str(deposit),
            },
            payment_intent_data={
                "metadata": {
                    "order_id": order_id,
                    "tier": data.tier or "",
                    "package_name": data.package_name or "",
                    "package_range": data.package_range or "",
                    "market": data.market or "",
                    "deposit_eur": str(deposit),
                }
            },
            success_url=success_url,
            cancel_url=cancel_url,
        )

        return {
            "url": session.url,
            "session_id": session.id,
            "deposit": deposit,
            "order_id": order_id,
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))