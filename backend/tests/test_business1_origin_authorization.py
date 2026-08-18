from fastapi.testclient import TestClient

from app.api.demo_telegram import TRUSTED_EXAMPLE_BY_ORIGIN
from app.main import app
from app.services.contact_delivery.example_scope import (
    EXAMPLES_BY_ORIGIN,
    ExampleScopeError,
    resolve_trusted_example,
)
from app.services.whatsapp_delivery.binding import trusted_example_for_origin


BUSINESS1 = "https://business1.siteformo.com"
DEV = "https://dev.siteformo.com"
UNKNOWN = "https://spoofed.invalid"
EXAMPLE = "SF_BU_01_CANONICAL_CONSULTING_EXAMPLE_V1"


def test_global_cors_accepts_business1_and_preserves_dev() -> None:
    client = TestClient(app)
    for origin in (BUSINESS1, DEV):
        response = client.options(
            "/api/demo/sms/start",
            headers={
                "Origin": origin,
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "content-type",
            },
        )
        assert response.status_code == 200
        assert response.headers["access-control-allow-origin"] == origin


def test_global_cors_rejects_unknown_origin() -> None:
    client = TestClient(app)
    response = client.options(
        "/api/demo/sms/start",
        headers={
            "Origin": UNKNOWN,
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type",
        },
    )
    assert response.status_code == 400
    assert "access-control-allow-origin" not in response.headers


def test_business1_is_bound_to_the_existing_business01_identity() -> None:
    whatsapp = trusted_example_for_origin(BUSINESS1)
    assert whatsapp is not None
    assert whatsapp.example_id == EXAMPLE
    assert resolve_trusted_example(BUSINESS1, EXAMPLE).example_id == EXAMPLE
    assert TRUSTED_EXAMPLE_BY_ORIGIN[BUSINESS1] == EXAMPLE


def test_dev_bindings_remain_and_unknown_bindings_are_rejected() -> None:
    assert trusted_example_for_origin(DEV) is not None
    assert resolve_trusted_example(DEV, EXAMPLE).example_id == EXAMPLE
    assert TRUSTED_EXAMPLE_BY_ORIGIN[DEV] == EXAMPLE

    assert trusted_example_for_origin(UNKNOWN) is None
    assert UNKNOWN not in EXAMPLES_BY_ORIGIN
    assert UNKNOWN not in TRUSTED_EXAMPLE_BY_ORIGIN
    try:
        resolve_trusted_example(UNKNOWN, EXAMPLE)
    except ExampleScopeError as exc:
        assert str(exc) == "origin_not_allowed"
    else:
        raise AssertionError("unknown origin accepted")
