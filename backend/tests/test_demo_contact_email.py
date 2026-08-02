from __future__ import annotations

import asyncio
import os
from collections.abc import Iterator
from typing import Self
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from app.api import demo_contact_email as api
from app.main import app
from app.services.contact_delivery import canary
from app.services.contact_delivery.canary import (
    Claim,
    ClaimKind,
    ProviderAcceptance,
    ProviderError,
)
from app.services.contact_delivery.template import Enquiry, render

ORIGIN = "https://dev.siteformo.com"
RECIPIENT = "owner-canary@example.com"


def payload(**overrides: str) -> dict[str, str]:
    value = {
        "first_name": "Avery",
        "last_name": "Rowan",
        "preferred_method": "Email",
        "contact_value": RECIPIENT,
        "message": "A safe fictional enquiry.",
        "idempotency_key": "contact-canary-test-0001",
    }
    value.update(overrides)
    return value


@pytest.fixture(autouse=True)
def canary_environment(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("SF_CONTACT_EMAIL_CANARY_ENABLED", "true")
    monkeypatch.setenv("SF_CONTACT_EMAIL_CANARY_RECIPIENT", RECIPIENT)
    monkeypatch.setenv("RESEND_API_KEY", "test-only-placeholder")
    yield
    for name in (
        "SF_CONTACT_EMAIL_CANARY_ENABLED",
        "SF_CONTACT_EMAIL_CANARY_RECIPIENT",
        "RESEND_API_KEY",
    ):
        os.environ.pop(name, None)


def install_success_mocks(monkeypatch: pytest.MonkeyPatch) -> tuple[AsyncMock, AsyncMock, AsyncMock]:
    claim = AsyncMock(return_value=Claim(ClaimKind.ACQUIRED))
    send = AsyncMock(return_value=ProviderAcceptance(message_id="provider-test-id", http_status=200))
    finalize = AsyncMock(return_value=None)
    monkeypatch.setattr(api, "claim_once", claim)
    monkeypatch.setattr(api, "send_with_resend", send)
    monkeypatch.setattr(api, "finalize_state", finalize)
    return claim, send, finalize


def test_allowlisted_recipient_returns_success_only_after_provider_acceptance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, send, finalize = install_success_mocks(monkeypatch)
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/demo-contact/email",
            headers={"Origin": ORIGIN},
            json=payload(),
        )
    assert response.status_code == 202
    assert response.json() == {
        "status": "provider_accepted",
        "message_id": "provider-test-id",
        "replayed": False,
    }
    send.assert_awaited_once()
    finalize.assert_awaited_once()


def test_blocked_recipient_never_claims_or_invokes_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    claim, send, finalize = install_success_mocks(monkeypatch)
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/demo-contact/email",
            headers={"Origin": ORIGIN},
            json=payload(contact_value="blocked@example.com"),
        )
    assert response.status_code == 403
    assert response.json() == {"detail": "canary_recipient_blocked"}
    claim.assert_not_awaited()
    send.assert_not_awaited()
    finalize.assert_not_awaited()


@pytest.mark.parametrize("origin", [None, "https://siteformo.com", "https://dev.siteformo.com.evil.test", "null"])
def test_malformed_or_unapproved_origin_is_rejected_before_provider(
    monkeypatch: pytest.MonkeyPatch, origin: str | None
) -> None:
    _, send, _ = install_success_mocks(monkeypatch)
    headers = {"Origin": origin} if origin else {}
    with TestClient(app) as client:
        response = client.post("/api/v1/demo-contact/email", headers=headers, json=payload())
    assert response.status_code == 403
    send.assert_not_awaited()


def test_cors_preflight_allows_exact_origin() -> None:
    with TestClient(app) as client:
        response = client.options(
            "/api/v1/demo-contact/email",
            headers={
                "Origin": ORIGIN,
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "content-type",
            },
        )
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == ORIGIN


def test_cors_preflight_rejects_unapproved_origin() -> None:
    with TestClient(app) as client:
        response = client.options(
            "/api/v1/demo-contact/email",
            headers={
                "Origin": "https://dev.siteformo.com.evil.test",
                "Access-Control-Request-Method": "POST",
            },
        )
    assert response.status_code == 400
    assert "access-control-allow-origin" not in response.headers


@pytest.mark.parametrize(
    ("change", "status_code"),
    [
        ({"preferred_method": "Phone"}, 422),
        ({"contact_value": "not-an-email"}, 422),
        ({"first_name": "bad\r\nname"}, 422),
        ({"message": "x" * 5_001}, 422),
        ({"idempotency_key": "short"}, 422),
    ],
)
def test_closed_schema_validation(
    monkeypatch: pytest.MonkeyPatch, change: dict[str, str], status_code: int
) -> None:
    _, send, _ = install_success_mocks(monkeypatch)
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/demo-contact/email", headers={"Origin": ORIGIN}, json=payload(**change)
        )
    assert response.status_code == status_code
    send.assert_not_awaited()


def test_browser_cannot_override_example_id(monkeypatch: pytest.MonkeyPatch) -> None:
    _, send, _ = install_success_mocks(monkeypatch)
    supplied = payload()
    supplied["trusted_example_id"] = "ATTACKER_OVERRIDE"
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/demo-contact/email", headers={"Origin": ORIGIN}, json=supplied
        )
    assert response.status_code == 422
    send.assert_not_awaited()


def test_duplicate_idempotency_key_returns_stored_acceptance_without_send(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        api,
        "claim_once",
        AsyncMock(return_value=Claim(ClaimKind.REPLAY_ACCEPTED, provider_message_id="stored-id")),
    )
    send = AsyncMock()
    monkeypatch.setattr(api, "send_with_resend", send)
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/demo-contact/email", headers={"Origin": ORIGIN}, json=payload()
        )
    assert response.status_code == 202
    assert response.json()["replayed"] is True
    send.assert_not_awaited()


def test_different_request_after_canary_is_consumed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(api, "claim_once", AsyncMock(return_value=Claim(ClaimKind.CONSUMED)))
    send = AsyncMock()
    monkeypatch.setattr(api, "send_with_resend", send)
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/demo-contact/email", headers={"Origin": ORIGIN}, json=payload()
        )
    assert response.status_code == 403
    assert response.json() == {"detail": "canary_consumed"}
    send.assert_not_awaited()


@pytest.mark.parametrize(
    ("error", "expected_status"),
    [(ProviderError("provider_rejected", 502), 502), (ProviderError("provider_timeout", 504), 504)],
)
def test_provider_failure_never_returns_success_and_is_finalized(
    monkeypatch: pytest.MonkeyPatch, error: ProviderError, expected_status: int
) -> None:
    claim = AsyncMock(return_value=Claim(ClaimKind.ACQUIRED))
    send = AsyncMock(side_effect=error)
    finalize = AsyncMock(return_value=None)
    monkeypatch.setattr(api, "claim_once", claim)
    monkeypatch.setattr(api, "send_with_resend", send)
    monkeypatch.setattr(api, "finalize_state", finalize)
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/demo-contact/email", headers={"Origin": ORIGIN}, json=payload()
        )
    assert response.status_code == expected_status
    assert response.json() == {"detail": error.code}
    finalize.assert_awaited_once()


def test_disabled_feature_flag_blocks_before_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SF_CONTACT_EMAIL_CANARY_ENABLED", "false")
    _, send, _ = install_success_mocks(monkeypatch)
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/demo-contact/email", headers={"Origin": ORIGIN}, json=payload()
        )
    assert response.status_code == 503
    assert response.json() == {"detail": "live_email_disabled"}
    send.assert_not_awaited()


def test_request_size_limit_blocks_before_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    _, send, _ = install_success_mocks(monkeypatch)
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/demo-contact/email",
            headers={"Origin": ORIGIN, "Content-Length": "16385"},
            json=payload(),
        )
    assert response.status_code == 413
    send.assert_not_awaited()


def test_final_templates_are_link_free_and_escape_visitor_html() -> None:
    rendered = render(
        Enquiry(
            first_name="Avery <script>",
            last_name="Rowan",
            preferred_method="Email",
            contact_value="owner-canary@example.com",
            message="Hello <img src=x onerror=alert(1)>",
        )
    )
    combined = f"{rendered.html}\n{rendered.text}".lower()
    for forbidden in ("href=", "https://", "http://", "mailto:", "tel:"):
        assert forbidden not in combined
    assert "<script" not in rendered.html.lower()
    assert "<img" not in rendered.html.lower()
    assert "&lt;script&gt;" in rendered.html
    assert rendered.subject == "Your SiteFormo demonstration enquiry"
    assert rendered.sender == "SiteFormo <siteformo@siteformo.com>"
    assert rendered.reply_to == "siteformo@siteformo.com"


def test_safe_failure_does_not_leak_secrets_or_recipient(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(api, "claim_once", AsyncMock(side_effect=RuntimeError("redis://secret")))
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/demo-contact/email", headers={"Origin": ORIGIN}, json=payload()
        )
    body = response.text
    assert response.status_code == 503
    assert "redis://secret" not in body
    assert RECIPIENT not in body
    assert "test-only-placeholder" not in body


def test_provider_request_uses_governed_contract(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    class Response:
        status_code = 200

        @staticmethod
        def json() -> dict[str, str]:
            return {"id": "provider-contract-id"}

    class Client:
        def __init__(self, **_: object) -> None:
            pass

        async def __aenter__(self) -> Self:
            return self

        async def __aexit__(self, *_: object) -> None:
            return None

        async def post(self, url: str, **kwargs: object) -> Response:
            captured["url"] = url
            captured.update(kwargs)
            return Response()

    monkeypatch.setattr(canary.httpx, "AsyncClient", Client)
    rendered = render(
        Enquiry("Avery", "Rowan", "Email", RECIPIENT, "Safe message")
    )
    result = asyncio.run(canary.send_with_resend(rendered, RECIPIENT, "contact-canary-test-0001"))
    assert result.message_id == "provider-contract-id"
    assert captured["url"] == "https://api.resend.com/emails"
    request_json = captured["json"]
    assert isinstance(request_json, dict)
    assert request_json["from"] == "SiteFormo <siteformo@siteformo.com>"
    assert request_json["reply_to"] == "siteformo@siteformo.com"
    assert request_json["subject"] == "Your SiteFormo demonstration enquiry"
    assert request_json["to"] == [RECIPIENT]
    assert "html" in request_json and "text" in request_json
    assert "trusted_example_id" not in request_json
