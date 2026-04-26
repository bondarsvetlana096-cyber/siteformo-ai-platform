# tests/test_pricing_service.py

from app.services.pricing_service import calculate_price


def test_starter_simple_landing():
    payload = {
        "ecommerce": False,
        "cart": False,
        "catalog": False,
        "booking": False,
        "advanced_integrations": False,
        "pages_requested": 1,
        "services_count": 1,
        "has_service_pages": False,
    }

    result = calculate_price(payload)

    assert result["tier"] == "Starter"
    assert result["price"] == 600


def test_starter_defaults():
    payload = {}

    result = calculate_price(payload)

    assert result["tier"] == "Starter"
    assert result["price"] == 600


def test_business_multiple_pages():
    payload = {
        "ecommerce": False,
        "cart": False,
        "catalog": False,
        "booking": False,
        "advanced_integrations": False,
        "pages_requested": 3,
        "services_count": 1,
        "has_service_pages": False,
    }

    result = calculate_price(payload)

    assert result["tier"] == "Business"
    assert result["price"] == 900


def test_business_services_count():
    payload = {
        "ecommerce": False,
        "cart": False,
        "catalog": False,
        "booking": False,
        "advanced_integrations": False,
        "pages_requested": 1,
        "services_count": 3,
        "has_service_pages": False,
    }

    result = calculate_price(payload)

    assert result["tier"] == "Business"
    assert result["price"] == 900


def test_business_service_pages():
    payload = {
        "ecommerce": False,
        "cart": False,
        "catalog": False,
        "booking": False,
        "advanced_integrations": False,
        "pages_requested": 1,
        "services_count": 2,
        "has_service_pages": True,
    }

    result = calculate_price(payload)

    assert result["tier"] == "Business"
    assert result["price"] == 900


def test_premium_ecommerce():
    payload = {
        "ecommerce": True,
        "cart": False,
        "catalog": False,
        "booking": False,
        "advanced_integrations": False,
        "pages_requested": 1,
        "services_count": 1,
        "has_service_pages": False,
    }

    result = calculate_price(payload)

    assert result["tier"] == "Premium"
    assert result["price"] == 1500


def test_premium_booking():
    payload = {
        "ecommerce": False,
        "cart": False,
        "catalog": False,
        "booking": True,
        "advanced_integrations": False,
        "pages_requested": 1,
        "services_count": 1,
        "has_service_pages": False,
    }

    result = calculate_price(payload)

    assert result["tier"] == "Premium"
    assert result["price"] == 1500


def test_premium_advanced_integrations():
    payload = {
        "ecommerce": False,
        "cart": False,
        "catalog": False,
        "booking": False,
        "advanced_integrations": True,
        "pages_requested": 2,
        "services_count": 2,
        "has_service_pages": True,
    }

    result = calculate_price(payload)

    assert result["tier"] == "Premium"
    assert result["price"] == 1500


def test_premium_overrides_business():
    payload = {
        "ecommerce": True,
        "cart": True,
        "catalog": True,
        "booking": False,
        "advanced_integrations": False,
        "pages_requested": 5,
        "services_count": 5,
        "has_service_pages": True,
    }

    result = calculate_price(payload)

    assert result["tier"] == "Premium"
    assert result["price"] == 1500


def test_string_inputs_handling():
    payload = {
        "ecommerce": "true",
        "cart": "false",
        "catalog": "0",
        "booking": "yes",
        "advanced_integrations": "no",
        "pages_requested": "3",
        "services_count": "2",
        "has_service_pages": "true",
    }

    result = calculate_price(payload)

    assert result["tier"] == "Premium"
    assert result["price"] == 1500