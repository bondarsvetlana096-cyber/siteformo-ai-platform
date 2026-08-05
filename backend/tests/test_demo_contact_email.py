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
EXAMPLE_ID = "SF_BU_01_CANONICAL_CONSULTING_EXAMPLE_V1"
RECIPIENT = "visitor@example.com"


def payload(**overrides: str) -> dict[str, str]:
    value = {
        "first_name": "Avery",
        "last_name": "Rowan",
        "preferred_method": "Email",
        "contact_value": RECIPIENT,
        "message": "A safe fictional enquiry.",
        "idempotency_key": "contact-public-test-0001",
    }
    value.update(overrides)
    return value


@pytest.fixture(autouse=True)
def public_environment(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("SF_CONTACT_EMAIL_PUBLIC_DEMO_ENABLED", "true")
    monkeypatch.setenv("RESEND_API_KEY", "test-only-placeholder")
    yield
    os.environ.pop("SF_CONTACT_EMAIL_PUBLIC_DEMO_ENABLED", None)
    os.environ.pop("RESEND_API_KEY", None)


def install_success_mocks(
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[AsyncMock, AsyncMock, AsyncMock, AsyncMock]:
    claim = AsyncMock(return_value=Claim(ClaimKind.ACQUIRED))
    send = AsyncMock(return_value=ProviderAcceptance(message_id="provider-test-id", http_status=200))
    accepted = AsyncMock(return_value=1)
    failed = AsyncMock(return_value=None)
    monkeypatch.setattr(api, "claim_once", claim)
    monkeypatch.setattr(api, "send_with_resend", send)
    monkeypatch.setattr(api, "finalize_accepted", accepted)
    monkeypatch.setattr(api, "finalize_failed", failed)
    return claim, send, accepted, failed


def post(body: dict[str, str], origin: str = ORIGIN):
    with TestClient(app) as client:
        return client.post("/api/v1/demo-contact/email", headers={"Origin": origin}, json=body)


def test_any_valid_recipient_is_delivered_to_exact_normalized_address(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, send, accepted, _ = install_success_mocks(monkeypatch)
    response = post(payload(contact_value="Visitor@Example.COM"))
    assert response.status_code == 202
    assert response.json() == {
        "status": "provider_accepted",
        "message_id": "provider-test-id",
        "replayed": False,
        "remaining_deliveries": 1,
    }
    assert send.await_args.args[1] == "visitor@example.com"
    assert send.await_args.args[3] == EXAMPLE_ID
    accepted.assert_awaited_once()


def test_two_independent_deliveries_are_accepted(monkeypatch: pytest.MonkeyPatch) -> None:
    claim, send, accepted, _ = install_success_mocks(monkeypatch)
    first = post(payload(idempotency_key="contact-public-first-0001"))
    accepted.return_value = 0
    second = post(payload(idempotency_key="contact-public-second-0002"))
    assert first.status_code == second.status_code == 202
    assert first.json()["remaining_deliveries"] == 1
    assert second.json()["remaining_deliveries"] == 0
    assert claim.await_count == send.await_count == accepted.await_count == 2


def test_third_delivery_is_rejected_before_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    claim, send, accepted, _ = install_success_mocks(monkeypatch)
    claim.return_value = Claim(ClaimKind.QUOTA_EXHAUSTED)
    response = post(payload(idempotency_key="contact-public-third-0003"))
    assert response.status_code == 429
    assert response.json() == {"detail": "quota_exhausted"}
    send.assert_not_awaited()
    accepted.assert_not_awaited()


def test_different_recipient_produces_independent_quota_identity() -> None:
    first = canary.delivery_identity(
        example_id=EXAMPLE_ID,
        recipient="one@example.com",
        idempotency_key="contact-public-first-0001",
        fingerprint="f1",
        client_id="127.0.0.1",
    )
    second = canary.delivery_identity(
        example_id=EXAMPLE_ID,
        recipient="two@example.com",
        idempotency_key="contact-public-second-0002",
        fingerprint="f2",
        client_id="127.0.0.1",
    )
    assert canary._keys(first, 0)[1] != canary._keys(second, 0)[1]


def test_different_trusted_example_produces_independent_quota_identity() -> None:
    first = canary.delivery_identity(
        example_id=EXAMPLE_ID,
        recipient=RECIPIENT,
        idempotency_key="contact-public-first-0001",
        fingerprint="f1",
        client_id="127.0.0.1",
    )
    second = canary.delivery_identity(
        example_id="SF_SECOND_EXAMPLE",
        recipient=RECIPIENT,
        idempotency_key="contact-public-second-0002",
        fingerprint="f2",
        client_id="127.0.0.1",
    )
    assert canary._keys(first, 0)[1] != canary._keys(second, 0)[1]


def test_idempotent_replay_returns_acceptance_without_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, send, accepted, _ = install_success_mocks(monkeypatch)
    monkeypatch.setattr(
        api,
        "claim_once",
        AsyncMock(
            return_value=Claim(
                ClaimKind.REPLAY_ACCEPTED,
                provider_message_id="stored-id",
                remaining_deliveries=0,
            )
        ),
    )
    response = post(payload())
    assert response.status_code == 202
    assert response.json() == {
        "status": "provider_accepted",
        "message_id": "stored-id",
        "replayed": True,
        "remaining_deliveries": 0,
    }
    send.assert_not_awaited()
    accepted.assert_not_awaited()


def test_concurrent_capacity_is_reserved_atomically_in_redis_script() -> None:
    assert "accepted + pending >=" in canary.CLAIM_SCRIPT
    assert "ZADD" in canary.CLAIM_SCRIPT
    assert "HINCRBY" in canary.FINALIZE_ACCEPTED_SCRIPT
    assert "accepted >" in canary.FINALIZE_ACCEPTED_SCRIPT
    assert "EXPIRE', KEYS[2]" not in canary.CLAIM_SCRIPT
    assert "remaining_deliveries" in canary.CLAIM_SCRIPT
    assert "remaining_deliveries" in canary.FINALIZE_ACCEPTED_SCRIPT


@pytest.mark.parametrize("address", ["not-an-email", "bad\r\n@example.com", "missing@"])
def test_invalid_email_rejected_before_quota_or_provider(
    monkeypatch: pytest.MonkeyPatch, address: str
) -> None:
    claim, send, _, _ = install_success_mocks(monkeypatch)
    response = post(payload(contact_value=address))
    assert response.status_code == 422
    claim.assert_not_awaited()
    send.assert_not_awaited()


@pytest.mark.parametrize("origin", ["https://siteformo.com", "https://dev.siteformo.com.evil.test", "null"])
def test_unknown_or_suffix_origin_rejected_before_quota_and_provider(
    monkeypatch: pytest.MonkeyPatch, origin: str
) -> None:
    claim, send, _, _ = install_success_mocks(monkeypatch)
    response = post(payload(), origin=origin)
    assert response.status_code == 403
    claim.assert_not_awaited()
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


def test_cors_preflight_rejects_suffix_origin() -> None:
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
    ("error", "expected_status"),
    [(ProviderError("provider_rejected", 502), 502), (ProviderError("provider_timeout", 504), 504)],
)
def test_provider_failure_releases_pending_without_accepting_slot(
    monkeypatch: pytest.MonkeyPatch, error: ProviderError, expected_status: int
) -> None:
    _, send, accepted, failed = install_success_mocks(monkeypatch)
    send.side_effect = error
    response = post(payload())
    assert response.status_code == expected_status
    accepted.assert_not_awaited()
    failed.assert_awaited_once()


def test_rate_limit_rejects_before_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    claim, send, accepted, _ = install_success_mocks(monkeypatch)
    claim.return_value = Claim(ClaimKind.RATE_LIMITED)
    response = post(payload())
    assert response.status_code == 429
    assert response.json() == {"detail": "rate_limited"}
    send.assert_not_awaited()
    accepted.assert_not_awaited()


def test_disabled_master_flag_blocks_before_state_or_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SF_CONTACT_EMAIL_PUBLIC_DEMO_ENABLED", "false")
    claim, send, _, _ = install_success_mocks(monkeypatch)
    response = post(payload())
    assert response.status_code == 503
    claim.assert_not_awaited()
    send.assert_not_awaited()


def test_request_size_and_closed_schema_block_before_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    claim, send, _, _ = install_success_mocks(monkeypatch)
    supplied = payload()
    supplied["trusted_example_id"] = "ATTACKER_OVERRIDE"
    with TestClient(app) as client:
        oversized = client.post(
            "/api/v1/demo-contact/email",
            headers={"Origin": ORIGIN, "Content-Length": "16385"},
            json=payload(),
        )
        override = client.post(
            "/api/v1/demo-contact/email", headers={"Origin": ORIGIN}, json=supplied
        )
    assert oversized.status_code == 413
    assert override.status_code == 422
    claim.assert_not_awaited()
    send.assert_not_awaited()


def test_public_path_ignores_retired_owner_canary_variables(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SF_CONTACT_EMAIL_CANARY_ENABLED", "false")
    monkeypatch.setenv("SF_CONTACT_EMAIL_CANARY_RECIPIENT", "owner-only@example.com")
    _, send, _, _ = install_success_mocks(monkeypatch)
    response = post(payload(contact_value="public-visitor@example.com"))
    assert response.status_code == 202
    send.assert_awaited_once()


def test_quota_keys_are_durable_hashed_and_channel_isolated() -> None:
    identity = canary.delivery_identity(
        example_id=EXAMPLE_ID,
        recipient=RECIPIENT,
        idempotency_key="contact-public-first-0001",
        fingerprint="fingerprint",
        client_id="127.0.0.1",
    )
    keys_before = canary._keys(identity, 0)
    keys_after_restart = canary._keys(identity, 0)
    assert keys_before == keys_after_restart
    assert ":EMAIL:" in keys_before[1]
    assert RECIPIENT not in " ".join(keys_before)
    assert EXAMPLE_ID not in " ".join(keys_before)


def test_final_templates_are_link_free_and_escape_visitor_html() -> None:
    rendered = render(
        Enquiry(
            first_name="Avery <script>",
            last_name="Rowan",
            preferred_method="Email",
            contact_value=RECIPIENT,
            message="Hello <img src=x onerror=alert(1)>",
        )
    )
    combined = f"{rendered.html}\n{rendered.text}".lower()
    for forbidden in ("href=", "https://", "http://", "mailto:", "tel:"):
        assert forbidden not in combined
    assert "<script" not in rendered.html.lower()
    assert "<img" not in rendered.html.lower()
    assert "&lt;script&gt;" in rendered.html
    assert rendered.sender == "SiteFormo <siteformo@siteformo.com>"
    assert rendered.reply_to == "siteformo@siteformo.com"


def test_final_templates_put_the_exact_visitor_message_before_the_explanation() -> None:
    rendered = render(
        Enquiry(
            first_name="Oleh",
            last_name="Owner",
            preferred_method="Email",
            contact_value=RECIPIENT,
            message="I'd like to book a meeting",
        )
    )
    assert rendered.text == (
        "Hi Oleh,\n\n"
        "Your message:\n\n"
        '"I\'d like to book a meeting"\n\n'
        "This is an example of how your customers can begin an email conversation "
        "from your future website.\n\n"
        "SiteFormo\n"
    )
    assert rendered.html.index("Your message:") < rendered.html.index(
        "I&#x27;d like to book a meeting"
    ) < rendered.html.index("This is an example of how your customers can begin")
    assert "&quot;I&#x27;d like to book a meeting&quot;" in rendered.html
    assert "Thank you for testing" not in rendered.text
    assert "HOW THIS COULD WORK" not in rendered.text
    assert "ABOUT YOUR INFORMATION" not in rendered.text


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
    rendered = render(Enquiry("Avery", "Rowan", "Email", RECIPIENT, "Safe message"))
    result = asyncio.run(
        canary.send_with_resend(
            rendered, RECIPIENT, "contact-public-test-0001", EXAMPLE_ID
        )
    )
    assert result.message_id == "provider-contract-id"
    request_json = captured["json"]
    assert isinstance(request_json, dict)
    assert request_json["to"] == [RECIPIENT]
    assert request_json["from"] == "SiteFormo <siteformo@siteformo.com>"
    assert request_json["reply_to"] == "siteformo@siteformo.com"
    assert "trusted_example_id" not in request_json
