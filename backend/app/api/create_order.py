import os
import uuid
import stripe

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, EmailStr
from typing import Optional

router = APIRouter()

stripe.api_key = os.getenv("STRIPE_SECRET_KEY")

FRONTEND_URL = os.getenv("FRONTEND_URL", "https://ie.siteformo.com")


class CreateOrderRequest(BaseModel):
    source: Optional[str] = "direct"
    type: Optional[str] = None
    example: Optional[str] = ""
    original_package: Optional[str] = None
    detected_package: Optional[str] = None
    email: EmailStr


PRICES = {
    "starter": 900,
    "business": 1500,
    "premium": 2450,
    "custom": 4500,
}


def choose_package(data: CreateOrderRequest) -> str:
    package = data.detected_package or data.original_package or "starter"

    if data.type == "landing" and package == "starter":
        return "starter"

    if data.type == "business" and package == "starter":
        return "business"

    if data.type == "shop":
        return "premium"

    if package not in PRICES:
        return "starter"

    return package


@router.post("/api/create-order")
async def create_order(data: CreateOrderRequest):
    if not stripe.api_key:
        raise HTTPException(status_code=500, detail="Stripe secret key is missing")

    package = choose_package(data)
    total = PRICES[package]
    deposit = int(total * 0.5)
    order_id = str(uuid.uuid4())

    try:
        session = stripe.checkout.Session.create(
            mode="payment",
            customer_email=data.email,
            payment_method_types=["card"],
            line_items=[
                {
                    "price_data": {
                        "currency": "eur",
                        "product_data": {
                            "name": f"SiteFormo {package.capitalize()} Package Deposit",
                            "description": f"50% deposit for SiteFormo {package} website project",
                        },
                        "unit_amount": deposit * 100,
                    },
                    "quantity": 1,
                }
            ],
            metadata={
                "order_id": order_id,
                "email": data.email,
                "package": package,
                "total": str(total),
                "deposit": str(deposit),
                "type": data.type or "",
                "example": data.example or "",
                "source": data.source or "direct",
            },
            success_url=(
                f"{FRONTEND_URL}/design-previews"
                f"?order_id={order_id}"
                f"&plan={package}"
                f"&deposit={deposit}"
                f"&total={total}&stage=paid"
            ),
            cancel_url=f"{FRONTEND_URL}/?payment=cancelled",
        )

        return {
            "success": True,
            "order_id": order_id,
            "package": package,
            "total": total,
            "deposit": deposit,
            "stripe_url": session.url,
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))