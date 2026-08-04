from __future__ import annotations

import asyncio
import importlib
from dataclasses import replace

import httpx
import pytest
from fastapi.testclient import TestClient

from app.api import demo_contact_whatsapp as api
from app.main import app
from app.services.whatsapp_delivery.configuration import (
    FlagStatus,
    Readiness,
    resolve_railway_twilio_configuration,
)
from app.services.whatsapp_delivery.models import render_demo_message
from app.services.delivery.contracts import Claim, ClaimKind
from app.services.whatsapp_delivery.service import WhatsAppDeliveryService
from app.services.whatsapp_delivery.transport import (
    FakeTwilioWhatsAppTransport,
    MessageMode,
    TwilioConfig,
    TwilioWhatsAppTransport,
)

ORIGIN = "https://dev.siteformo.com"


class FakeClient:
    def __init__(self, **kwargs: object) -> None:
        self.kwargs = kwargs
        self.calls: list[dict[str, object]] = []
        self.closed = False

    async def post(self, *args: object, **kwargs: object) -> httpx.Response:
        self.calls.append({"args": args, "kwargs": kwargs})
        return httpx.Response(201, json={"sid": "SM" + "1" * 32})

    async def aclose(self) -> None:
        self.closed = True


def complete(**overrides: str) -> dict[str, str]:
    values = {
        "WHATSAPP_PROVIDER": "twilio",
        "WHATSAPP_TWILIO_ACCOUNT_SID": "AC" + "1" * 32,
        "WHATSAPP_TWILIO_AUTH_TOKEN": "test-token-not-a-secret",
        "WHATSAPP_TWILIO_NUMBER": "+353800000001",
        "WHATSAPP_TWILIO_CONTENT_SID": "HX" + "2" * 32,
        "SF_CONTACT_WHATSAPP_PUBLIC_DEMO_ENABLED": "true",
        "REDIS_URL": "redis://example.invalid:6379/0",
    }
    values.update(overrides)
    return values


def payload() -> dict[str, str]:
    return {
        "first_name": "Alex",
        "phone": "+353800000002",
        "idempotency_key": "whatsapp-runtime-test-0001",
    }


def teardown_function() -> None:
    asyncio.run(api.close_whatsapp_runtime())
    api._service_override = None


class CapturingService:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    async def send(self, **kwargs: object) -> tuple[str, str, bool, int]:
        self.calls.append(kwargs)
        return "provider_accepted", "wa_safe_reference", False, 1


class AcquiredState:
    def __init__(self) -> None:
        self.identities: list[object] = []

    async def claim(self, identity: object) -> Claim:
        self.identities.append(identity)
        return Claim(ClaimKind.ACQUIRED)

    async def accept(self, identity: object, provider_message_id: str) -> int:
        return 1

    async def release(self, identity: object, failure_code: str) -> None:
        raise AssertionError("unexpected release")

    async def quarantine(self, identity: object, failure_code: str) -> None:
        raise AssertionError("unexpected quarantine")


def test_disabled_configuration_creates_zero_clients_and_transport() -> None:
    created: list[FakeClient] = []
    assert api.configure_whatsapp_runtime({}, lambda **kwargs: created.append(FakeClient(**kwargs))) is False
    assert created == []
    assert api._whatsapp_service is None
    assert api._whatsapp_http_client is None


def test_missing_content_sid_fails_before_client_creation() -> None:
    created: list[FakeClient] = []
    values = complete()
    del values["WHATSAPP_TWILIO_CONTENT_SID"]
    assert api.configure_whatsapp_runtime(values, lambda **kwargs: created.append(FakeClient(**kwargs))) is False
    assert created == [] and api._whatsapp_service is None


def test_approved_content_binding_injects_service_and_cleanup() -> None:
    created: list[FakeClient] = []

    def factory(**kwargs: object) -> FakeClient:
        client = FakeClient(**kwargs)
        created.append(client)
        return client

    assert api.configure_whatsapp_runtime(complete(), factory) is True
    assert len(created) == 1 and api._whatsapp_service is not None
    asyncio.run(api.close_whatsapp_runtime())
    assert created[0].closed is True
    assert api._whatsapp_service is None and api._whatsapp_http_client is None


def test_application_startup_and_shutdown_invoke_whatsapp_lifecycle(monkeypatch) -> None:
    main_module = importlib.import_module("app.main")
    events: list[str] = []

    def configure() -> bool:
        events.append("startup")
        return False

    async def close() -> None:
        events.append("shutdown")

    monkeypatch.setattr(main_module, "configure_whatsapp_runtime", configure)
    monkeypatch.setattr(main_module, "close_whatsapp_runtime", close)
    with TestClient(main_module.app):
        assert events == ["startup"]
    assert events == ["startup", "shutdown"]


def test_configuration_is_whatsapp_specific_and_messaging_service_precedes_direct_sender() -> None:
    values = complete(WHATSAPP_TWILIO_MESSAGING_SERVICE_SID="MG" + "3" * 32)
    configuration = resolve_railway_twilio_configuration(values)
    assert configuration.readiness is Readiness.READY
    assert configuration.public_demo_flag is FlagStatus.ENABLED
    assert configuration.content_sid.source_name == "WHATSAPP_TWILIO_CONTENT_SID"
    assert configuration.sender_mode == "messaging_service"
    values.pop("WHATSAPP_TWILIO_CONTENT_SID")
    values["TWILIO_CONTENT_SID"] = "HX" + "4" * 32
    assert resolve_railway_twilio_configuration(values).content_sid.value is None


def test_business_template_form_has_content_fields_and_no_body() -> None:
    config = TwilioConfig(
        account_sid="AC" + "1" * 32,
        auth_token="token",
        message_mode=MessageMode.BUSINESS_INITIATED_TEMPLATE,
        sender_e164="+353800000001",
        content_sid="HX" + "2" * 32,
    )
    message = replace(render_demo_message("Alex", "corr"), destination_e164="+353800000002")
    form = TwilioWhatsAppTransport(config, FakeClient()).form(message)
    assert form["ContentSid"] == "HX" + "2" * 32
    assert form["ContentVariables"] == '{"1":"Alex"}'
    assert "Body" not in form and form["From"] == "whatsapp:+353800000001"


def test_session_path_regression_uses_body_without_content_fields() -> None:
    config = TwilioConfig(
        account_sid="AC" + "1" * 32,
        auth_token="token",
        message_mode=MessageMode.SESSION_FREEFORM_BODY,
        messaging_service_sid="MG" + "3" * 32,
    )
    message = replace(render_demo_message("Alex", "corr"), destination_e164="+353800000002")
    form = TwilioWhatsAppTransport(config, FakeClient()).form(message)
    assert form["Body"].startswith("Hi Alex,")
    assert "ContentSid" not in form and "ContentVariables" not in form
    assert form["MessagingServiceSid"] == "MG" + "3" * 32 and "From" not in form


def test_public_endpoint_fails_closed_without_runtime(monkeypatch) -> None:
    monkeypatch.setenv("SF_CONTACT_WHATSAPP_PUBLIC_DEMO_ENABLED", "false")
    api._whatsapp_service = None
    with TestClient(app) as client:
        response = client.post("/api/v1/demo-contact/whatsapp", headers={"Origin": ORIGIN}, json=payload())
    assert response.status_code == 503
    assert response.json() == {"detail": "live_whatsapp_disabled"}
    assert response.headers.get("cache-control") == "no-store"


def test_enabled_but_incomplete_endpoint_has_no_provider_path(monkeypatch) -> None:
    monkeypatch.setenv("SF_CONTACT_WHATSAPP_PUBLIC_DEMO_ENABLED", "true")
    created: list[FakeClient] = []
    assert api.configure_whatsapp_runtime({"SF_CONTACT_WHATSAPP_PUBLIC_DEMO_ENABLED": "true"}, lambda **kwargs: created.append(FakeClient(**kwargs))) is False
    with TestClient(app) as client:
        response = client.post("/api/v1/demo-contact/whatsapp", headers={"Origin": ORIGIN}, json=payload())
    assert response.status_code == 503
    assert response.json() == {"detail": "provider_not_configured"}
    assert response.headers.get("cache-control") == "no-store"
    assert created == []


def test_public_request_phone_and_first_name_reach_service_without_fixed_recipient(monkeypatch) -> None:
    monkeypatch.setenv("SF_CONTACT_WHATSAPP_PUBLIC_DEMO_ENABLED", "true")
    service = CapturingService()
    api._service_override = service  # type: ignore[assignment]
    request = payload() | {"first_name": "  Niamh  ", "phone": "+353871234567"}
    with TestClient(app) as client:
        response = client.post("/api/v1/demo-contact/whatsapp", headers={"Origin": ORIGIN}, json=request)
    assert response.status_code == 202
    assert service.calls[0]["phone"] == "+353871234567"
    assert service.calls[0]["first_name"] == "Niamh"
    assert "message" not in service.calls[0]


@pytest.mark.parametrize("phone", ["353871234567", "+1", "+353 87 123 4567", "+353-87-123-4567"])
def test_public_contract_rejects_non_strict_e164_before_service(monkeypatch, phone: str) -> None:
    monkeypatch.setenv("SF_CONTACT_WHATSAPP_PUBLIC_DEMO_ENABLED", "true")
    service = CapturingService()
    api._service_override = service  # type: ignore[assignment]
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/demo-contact/whatsapp",
            headers={"Origin": ORIGIN},
            json=payload() | {"phone": phone},
        )
    assert response.status_code == 422
    assert service.calls == []


def test_public_contract_allows_omitted_or_blank_first_name(monkeypatch) -> None:
    monkeypatch.setenv("SF_CONTACT_WHATSAPP_PUBLIC_DEMO_ENABLED", "true")
    service = CapturingService()
    api._service_override = service  # type: ignore[assignment]
    without_name = payload()
    without_name.pop("first_name")
    with TestClient(app) as client:
        first = client.post("/api/v1/demo-contact/whatsapp", headers={"Origin": ORIGIN}, json=without_name)
        second = client.post(
            "/api/v1/demo-contact/whatsapp",
            headers={"Origin": ORIGIN},
            json=payload() | {"first_name": "   ", "idempotency_key": "whatsapp-runtime-test-0002"},
        )
    assert first.status_code == second.status_code == 202
    assert [call["first_name"] for call in service.calls] == [None, None]


def test_public_contract_rejects_browser_owned_provider_fields(monkeypatch) -> None:
    monkeypatch.setenv("SF_CONTACT_WHATSAPP_PUBLIC_DEMO_ENABLED", "true")
    service = CapturingService()
    api._service_override = service  # type: ignore[assignment]
    forbidden = payload() | {"sender": "+353871111111", "content_sid": "HX" + "1" * 32}
    with TestClient(app) as client:
        response = client.post("/api/v1/demo-contact/whatsapp", headers={"Origin": ORIGIN}, json=forbidden)
    assert response.status_code == 422
    assert service.calls == []


def test_service_uses_server_fallback_and_hash_only_identity() -> None:
    state = AcquiredState()
    transport = FakeTwilioWhatsAppTransport()
    service = WhatsAppDeliveryService(state, transport, lambda: None)  # type: ignore[arg-type]
    result = asyncio.run(
        service.send(
            example_id="contact-example",
            phone="+353871234567",
            first_name=None,
            idempotency_key="whatsapp-runtime-test-0003",
            client_id="test-client",
        )
    )
    assert result[0] == "provider_accepted"
    assert transport.calls[0][0].content_variables == {"1": "there"}
    assert transport.calls[0][0].destination_e164 == "+353871234567"
    identity = state.identities[0]
    assert getattr(identity, "recipient_hash") != "+353871234567"
    assert "+353871234567" not in repr(identity)
