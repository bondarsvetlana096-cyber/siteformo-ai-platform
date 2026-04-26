# backend/app/services/pricing_service.py

from typing import Any, Dict


STARTER_PRICE = 600
BUSINESS_PRICE = 900
PREMIUM_PRICE = 1500


def _to_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value

    if isinstance(value, str):
        return value.strip().lower() in {"true", "1", "yes", "y", "да"}

    return bool(value)


def _to_int(value: Any, default: int = 1) -> int:
    try:
        if value is None or value == "":
            return default
        return int(value)
    except (TypeError, ValueError):
        return default


def calculate_price(data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Core pricing logic
    """

    # --- INPUT ---
    ecommerce = _to_bool(data.get("ecommerce"))
    cart = _to_bool(data.get("cart"))
    catalog = _to_bool(data.get("catalog"))
    booking = _to_bool(data.get("booking"))
    advanced_integrations = _to_bool(data.get("advanced_integrations"))

    pages_requested = _to_int(data.get("pages_requested"), default=1)
    services_count = _to_int(data.get("services_count"), default=1)
    has_service_pages = _to_bool(data.get("has_service_pages"))

    # --- PREMIUM LOGIC (FIRST) ---
    premium_reasons = []

    if ecommerce:
        premium_reasons.append("ecommerce / online store")
    if cart:
        premium_reasons.append("shopping cart")
    if catalog:
        premium_reasons.append("catalog")
    if booking:
        premium_reasons.append("booking system")
    if advanced_integrations:
        premium_reasons.append("advanced integrations / CRM / AI / payments")

    if premium_reasons:
        return {
            "tier": "Premium",
            "price": PREMIUM_PRICE,
            "currency": "EUR",
            "reason": (
                "This project requires advanced functionality: "
                + ", ".join(premium_reasons)
                + "."
            ),
            "signals": {
                "premium": premium_reasons,
                "business": [],
                "starter": [],
            },
        }

    # --- BUSINESS LOGIC ---
    business_reasons = []

    if pages_requested >= 2:
        business_reasons.append(f"{pages_requested} pages")
    if services_count >= 2:
        business_reasons.append(f"{services_count} services")
    if has_service_pages:
        business_reasons.append("service pages")

    if business_reasons:
        return {
            "tier": "Business",
            "price": BUSINESS_PRICE,
            "currency": "EUR",
            "reason": (
                "This project requires a more complex structure: "
                + ", ".join(business_reasons)
                + "."
            ),
            "signals": {
                "premium": [],
                "business": business_reasons,
                "starter": [],
            },
        }

    # --- STARTER ---
    return {
        "tier": "Starter",
        "price": STARTER_PRICE,
        "currency": "EUR",
        "reason": "Simple landing page without advanced functionality.",
        "signals": {
            "premium": [],
            "business": [],
            "starter": ["1 page", "no advanced features"],
        },
    }


# --- COMPATIBILITY LAYER (CRITICAL) ---
class PricingService:
    @staticmethod
    def calculate_price(data: Dict[str, Any]) -> Dict[str, Any]:
        return calculate_price(data)

    @staticmethod
    def get_price(data: Dict[str, Any]) -> Dict[str, Any]:
        return calculate_price(data)

    @staticmethod
    def estimate(data: Dict[str, Any]) -> Dict[str, Any]:
        return calculate_price(data)

    @staticmethod
    def classify(data: Dict[str, Any]):
        """
        This is required by intake_service.py
        """
        result = calculate_price(data)

        return (
            result["tier"],
            result["price"],
            result["reason"],
        )