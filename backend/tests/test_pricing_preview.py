# backend/tests/test_pricing_preview.py

from app.services.pricing_service import calculate_price


def test_starter_price():
    result = calculate_price({
        "ecommerce": False,
        "cart": False,
        "catalog": False,
        "booking": False,
        "advanced_integrations": False,
        "pages_requested": 1,
        "services_count": 1,
        "has_service_pages": False,
    })

    assert result["tier"] == "Starter"
    assert result["price"] == 600


def test_business_price():
    result = calculate_price({
        "ecommerce": False,
        "cart": False,
        "catalog": False,
        "booking": False,
        "advanced_integrations": False,
        "pages_requested": 3,
        "services_count": 2,
        "has_service_pages": True,
    })

    assert result["tier"] == "Business"
    assert result["price"] == 900


def test_premium_price_ecommerce():
    result = calculate_price({
        "ecommerce": True,
        "cart": True,
        "catalog": True,
        "booking": False,
        "advanced_integrations": False,
        "pages_requested": 1,
        "services_count": 1,
        "has_service_pages": False,
    })

    assert result["tier"] == "Premium"
    assert result["price"] == 1500


def test_premium_price_booking():
    result = calculate_price({
        "ecommerce": False,
        "cart": False,
        "catalog": False,
        "booking": True,
        "advanced_integrations": False,
        "pages_requested": 1,
        "services_count": 1,
        "has_service_pages": False,
    })

    assert result["tier"] == "Premium"
    assert result["price"] == 1500


def test_premium_overrides_business():
    result = calculate_price({
        "ecommerce": True,
        "cart": True,
        "catalog": True,
        "booking": True,
        "advanced_integrations": True,
        "pages_requested": 5,
        "services_count": 5,
        "has_service_pages": True,
    })

    assert result["tier"] == "Premium"
    assert result["price"] == 1500