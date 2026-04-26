import os
import stripe

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field


router = APIRouter(prefix="/api/payments", tags=["payments"])

stripe.api_key = os.getenv("STRIPE_SECRET_KEY")


class CheckoutRequest(BaseModel):
    amount: int = Field(..., ge=1)
    order_id: str | None = None
    tier: str | None = None
    customer_email: str | None = None


def _safe_deposit_for_tier(tier: str | None, amount: int) -> int:
    normalized = (tier or "").strip().lower()

    if normalized == "starter":
        return 300

    if normalized == "business":
        return 450

    if normalized == "premium":
        return 750

    if amount in [300, 450, 750]:
        return amount

    return 300


@router.post("/create-checkout")
async def create_checkout(data: CheckoutRequest):
    if not stripe.api_key:
        raise HTTPException(status_code=500, detail="Stripe is not configured.")

    deposit = _safe_deposit_for_tier(data.tier, data.amount)

    success_url = f"https://siteformo.com/?payment=success&order_id={data.order_id}"
    cancel_url = os.getenv("FRONTEND_CANCEL_URL", "https://siteformo.com/?payment=cancel")

    try:
        session = stripe.checkout.Session.create(
            mode="payment",
            payment_method_types=["card"],
            customer_email=data.customer_email or None,
            client_reference_id=data.order_id or None,
            line_items=[
                {
                    "price_data": {
                        "currency": "eur",
                        "product_data": {
                            "name": f"SiteFormo {data.tier or 'Website'} deposit",
                            "description": "50% project deposit",
                        },
                        "unit_amount": deposit * 100,
                    },
                    "quantity": 1,
                }
            ],
            metadata={
                "order_id": data.order_id or "",
                "tier": data.tier or "",
                "deposit_eur": str(deposit),
            },
            payment_intent_data={
                "metadata": {
                    "order_id": data.order_id or "",
                    "tier": data.tier or "",
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
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))