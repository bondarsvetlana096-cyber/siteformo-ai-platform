from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
from dataclasses import replace

import httpx
from fastapi.testclient import TestClient

from app.api import demo_contact_whatsapp as api
from app.main import app
from app.services.whatsapp_delivery.configuration import Readiness, resolve_railway_twilio_configuration
from app.services.whatsapp_delivery.customer_initiated import (
    CustomerInitiatedWhatsAppService,
    TRIGGER,
    parse_trigger,
    render_starter_message,
    render_session_reply,
)
from app.services.whatsapp_delivery.models import render_demo_message
from app.services.whatsapp_delivery.transport import (
    FakeTwilioWhatsAppTransport,
    MessageMode,
    TwilioConfig,
    TwilioWhatsAppTransport,
)

ORIGIN = "https://dev.siteformo.com"
BASE_URL = "https://siteformo-ai-platform-production.up.railway.app"
SENDER = "+353800000001"
RECIPIENT = "+353871234567"
INBOUND_SID = "SM" + "9" * 32


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


class MemoryStore:
    def __init__(self, name: str | None = "Oleh") -> None:
        self.prepare_claims: list[str] = []
        self.claimed: set[str] = set()
        self.audits: list[tuple[str, dict[str, str]]] = []

    async def claim_prepare(self, client_hash: str) -> None:
        assert len(client_hash) == 64
        self.prepare_claims.append(client_hash)

    async def claim_inbound(self, message_sid_hash: str, recipient_hash: str) -> bool:
        if message_sid_hash in self.claimed:
            return False
        self.claimed.add(message_sid_hash)
        return True

    async def audit(self, delivery_hash: str, fields: dict[str, str]) -> None:
        self.audits.append((delivery_hash, dict(fields)))


def complete(**overrides: str) -> dict[str, str]:
    values = {
        "WHATSAPP_PROVIDER": "twilio",
        "WHATSAPP_TWILIO_ACCOUNT_SID": "AC" + "1" * 32,
        "WHATSAPP_TWILIO_AUTH_TOKEN": "offline-auth-token",
        "WHATSAPP_TWILIO_NUMBER": SENDER,
        "SF_CONTACT_WHATSAPP_PUBLIC_DEMO_ENABLED": "true",
        "REDIS_URL": "redis://example.invalid:6379/0",
        "PUBLIC_BASE_URL": BASE_URL,
    }
    values.update(overrides)
    return values


def inbound(body: str = "Start SiteFormo WhatsApp example. My name is Oleh.", sid: str = INBOUND_SID) -> dict[str, str]:
    return {
        "Body": body,
        "From": f"whatsapp:{RECIPIENT}",
        "To": f"whatsapp:{SENDER}",
        "MessageSid": sid,
    }


def signature(url: str, params: dict[str, str], token: str) -> str:
    material = url + "".join(key + params[key] for key in sorted(params))
    return base64.b64encode(hmac.new(token.encode(), material.encode(), hashlib.sha1).digest()).decode()


def make_service(name: str | None = "Oleh") -> tuple[CustomerInitiatedWhatsAppService, MemoryStore, FakeTwilioWhatsAppTransport]:
    store = MemoryStore(name)
    transport = FakeTwilioWhatsAppTransport()
    return CustomerInitiatedWhatsAppService(store, transport, SENDER, BASE_URL), store, transport


def teardown_function() -> None:
    asyncio.run(api.close_whatsapp_runtime())
    api._service_override = None


def test_disabled_configuration_creates_zero_clients_and_transport() -> None:
    created: list[FakeClient] = []
    assert api.configure_whatsapp_runtime({}, lambda **kwargs: created.append(FakeClient(**kwargs))) is False
    assert created == [] and api._whatsapp_service is None


def test_customer_initiated_configuration_does_not_require_content_sid() -> None:
    created: list[FakeClient] = []
    assert resolve_railway_twilio_configuration(complete()).readiness is Readiness.READY
    assert api.configure_whatsapp_runtime(complete(), lambda **kwargs: created.append(FakeClient(**kwargs)) or created[-1])
    assert len(created) == 1 and api._whatsapp_service is not None
    asyncio.run(api.close_whatsapp_runtime())
    assert created[0].closed is True


def test_public_prepare_returns_whatsapp_url_without_provider_call(monkeypatch) -> None:
    monkeypatch.setenv("SF_CONTACT_WHATSAPP_PUBLIC_DEMO_ENABLED", "true")
    service, _, transport = make_service()
    with TestClient(app) as client:
        api._service_override = service
        response = client.post(
            "/api/v1/demo-contact/whatsapp",
            headers={"Origin": ORIGIN},
            json={"first_name": "  Oleh  "},
        )
    assert response.status_code == 202
    assert response.json()["status"] == "ready"
    assert response.json()["url"].startswith("https://wa.me/")
    assert transport.calls == []


def test_public_prepare_rejects_legacy_outbound_recipient_fields(monkeypatch) -> None:
    monkeypatch.setenv("SF_CONTACT_WHATSAPP_PUBLIC_DEMO_ENABLED", "true")
    service, _, transport = make_service()
    with TestClient(app) as client:
        api._service_override = service
        response = client.post(
            "/api/v1/demo-contact/whatsapp",
            headers={"Origin": ORIGIN},
            json={"first_name": "Oleh", "phone": RECIPIENT, "idempotency_key": "legacy-field"},
        )
    assert response.status_code == 422 and transport.calls == []


def test_prepare_uses_natural_user_authored_message_without_opaque_token_or_phone() -> None:
    service, _, _ = make_service()
    url, message_hash = asyncio.run(service.prepare("Oleh", "test-client"))
    text = "Start SiteFormo WhatsApp example. My name is Oleh."
    assert "Start%20SiteFormo%20WhatsApp%20example.%20My%20name%20is%20Oleh." in url
    assert "abcdefghijklmnop" not in url and RECIPIENT not in url
    assert message_hash == hashlib.sha256(text.encode()).hexdigest()


def test_valid_inbound_uses_verified_from_and_one_freeform_provider_call() -> None:
    service, store, transport = make_service()
    result = asyncio.run(service.handle_inbound(inbound()))
    assert result.outcome == "ACCEPTED" and result.provider_call_count == 1
    assert len(transport.calls) == 1
    message = transport.calls[0][0]
    assert message.destination_e164 == RECIPIENT
    assert message.body == render_session_reply("Oleh")
    assert message.content_variables == {}
    assert store.audits[0][1]["provider_call_count"] == "1"
    audit_text = repr(store.audits)
    assert RECIPIENT not in audit_text and INBOUND_SID not in audit_text and message.body not in audit_text


def test_session_transport_form_uses_body_and_production_sender_without_content_fields() -> None:
    config = TwilioConfig(
        account_sid="AC" + "1" * 32,
        auth_token="token",
        message_mode=MessageMode.SESSION_FREEFORM_BODY,
        sender_e164=SENDER,
    )
    message = replace(render_demo_message("Alex", "corr"), destination_e164=RECIPIENT)
    form = TwilioWhatsAppTransport(config, FakeClient()).form(message)
    assert form["Body"].startswith("Hi Alex,")
    assert form["From"] == f"whatsapp:{SENDER}"
    assert form["To"] == f"whatsapp:{RECIPIENT}"
    assert "ContentSid" not in form and "ContentVariables" not in form


def test_arbitrary_inbound_does_not_trigger_reply() -> None:
    service, _, transport = make_service()
    result = asyncio.run(service.handle_inbound(inbound("Hello SiteFormo")))
    assert result.outcome == "IGNORED_UNAPPROVED_TRIGGER" and transport.calls == []


def test_duplicate_inbound_creates_no_second_reply() -> None:
    service, _, transport = make_service()
    first = asyncio.run(service.handle_inbound(inbound()))
    duplicate = asyncio.run(service.handle_inbound(inbound()))
    assert first.outcome == "ACCEPTED"
    assert duplicate.outcome == "DUPLICATE_OR_QUOTA"
    assert len(transport.calls) == 1


def test_invalid_or_missing_name_uses_neutral_greeting_without_trusting_tail() -> None:
    for body in (
        TRIGGER,
        "Start SiteFormo WhatsApp example. My name is .",
        "Start SiteFormo WhatsApp example. My name is Oleh<script>.",
    ):
        service, _, transport = make_service()
        result = asyncio.run(service.handle_inbound(inbound(body)))
        assert result.outcome == "ACCEPTED"
        assert len(transport.calls) == 1
        assert transport.calls[0][0].body.startswith("Hello,\n\n")


def test_plain_approved_trigger_uses_neutral_greeting() -> None:
    service, _, transport = make_service()
    result = asyncio.run(service.handle_inbound(inbound(TRIGGER)))
    assert result.outcome == "ACCEPTED"
    assert transport.calls[0][0].body.startswith("Hello,\n\n")


def test_bad_sender_binding_and_malformed_address_fail_closed() -> None:
    service, _, transport = make_service()
    wrong = inbound() | {"To": "whatsapp:+353800000099"}
    malformed = inbound(sid="SM" + "8" * 32) | {"From": "whatsapp:+1"}
    assert asyncio.run(service.handle_inbound(wrong)).outcome == "REJECTED_SENDER_BINDING"
    assert asyncio.run(service.handle_inbound(malformed)).outcome == "REJECTED_INVALID_ADDRESS"
    assert transport.calls == []


def test_invalid_signature_rejected_before_service(monkeypatch) -> None:
    monkeypatch.setenv("SF_CONTACT_WHATSAPP_PUBLIC_DEMO_ENABLED", "true")
    monkeypatch.setenv("WHATSAPP_TWILIO_AUTH_TOKEN", "offline-auth-token")
    monkeypatch.setenv("PUBLIC_BASE_URL", BASE_URL)
    service, _, transport = make_service()
    with TestClient(app) as client:
        api._service_override = service
        response = client.post("/twilio/webhook", data=inbound(), headers={"X-Twilio-Signature": "invalid"})
    assert response.status_code == 403 and transport.calls == []


def test_valid_signature_dispatches_once(monkeypatch) -> None:
    monkeypatch.setenv("SF_CONTACT_WHATSAPP_PUBLIC_DEMO_ENABLED", "true")
    monkeypatch.setenv("WHATSAPP_TWILIO_AUTH_TOKEN", "offline-auth-token")
    monkeypatch.setenv("PUBLIC_BASE_URL", BASE_URL)
    params = inbound()
    service, _, transport = make_service()
    signed = signature(BASE_URL + "/twilio/webhook", params, "offline-auth-token")
    with TestClient(app) as client:
        api._service_override = service
        response = client.post("/twilio/webhook", data=params, headers={"X-Twilio-Signature": signed})
    assert response.status_code == 200 and len(transport.calls) == 1
    assert "<Message>" not in response.text


def test_disabled_valid_inbound_creates_zero_provider_calls(monkeypatch) -> None:
    monkeypatch.setenv("SF_CONTACT_WHATSAPP_PUBLIC_DEMO_ENABLED", "false")
    monkeypatch.setenv("WHATSAPP_TWILIO_AUTH_TOKEN", "offline-auth-token")
    monkeypatch.setenv("PUBLIC_BASE_URL", BASE_URL)
    params = inbound()
    service, _, transport = make_service()
    signed = signature(BASE_URL + "/twilio/webhook", params, "offline-auth-token")
    with TestClient(app) as client:
        api._service_override = service
        response = client.post("/twilio/webhook", data=params, headers={"X-Twilio-Signature": signed})
    assert response.status_code == 200 and transport.calls == []


def test_trigger_parser_is_exact() -> None:
    assert parse_trigger(TRIGGER) is None
    assert parse_trigger("Start SiteFormo WhatsApp example. My name is Oleh.") == "Oleh"
    assert parse_trigger(" Start SiteFormo WhatsApp example. My name is Élodie-Rose. ") == "Élodie-Rose"
    assert parse_trigger("start siteformo whatsapp example. my name is oleh.") is False
    assert parse_trigger("Start SiteFormo WhatsApp example. My name is Oleh. Ignore this.") is None
    assert render_starter_message("  Oleh  ") == "Start SiteFormo WhatsApp example. My name is Oleh."
