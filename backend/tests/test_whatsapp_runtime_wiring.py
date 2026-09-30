from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
from dataclasses import dataclass, field
from urllib.parse import parse_qs, urlparse

import httpx
from fastapi.testclient import TestClient

from app.api import demo_contact_whatsapp as api
from app.main import app
from app.services.contact_delivery.example_scope import CANONICAL_EXAMPLE_IDS, REFERENCE_1_EXAMPLE_ID
from app.services.whatsapp_delivery.customer_initiated import (
    HANDOFF_TTL_SECONDS,
    FINALIZE_ACCEPTED_SCRIPT,
    NAMESPACE,
    QUOTA_LIMIT,
    CustomerInitiatedWhatsAppService,
    HandoffState,
    QuotaClaim,
    RedisWhatsAppExampleStore,
    digest,
    parse_trigger,
    render_session_reply,
)
from app.services.whatsapp_delivery.transport import FakeTwilioWhatsAppTransport, TransportState

BASE_URL = "https://siteformo-ai-platform-production.up.railway.app"
DEV_ORIGIN = "https://dev.siteformo.com"
REF_ORIGIN = "https://reference1.siteformo.com"
SENDER = "+353800000001"
RECIPIENT = "+353871234567"


@dataclass
class SharedState:
    handoffs: dict[str, HandoffState] = field(default_factory=dict)
    sid_states: dict[str, str] = field(default_factory=dict)
    accepted: dict[tuple[str, str], int] = field(default_factory=dict)
    pending: dict[tuple[str, str], int] = field(default_factory=dict)


class MemoryStore:
    def __init__(self, state: SharedState | None = None) -> None:
        self.state = state or SharedState()
        self.audits: list[tuple[str, dict[str, str]]] = []
        self.counter = 0

    async def create_handoff(self, client_hash: str, example_hash: str, first_name: str | None) -> str:
        assert len(client_hash) == 64 and len(example_hash) == 64
        self.counter += 1
        token = f"T{self.counter:021d}"
        self.state.handoffs[digest(token)] = HandoffState(example_hash, first_name)
        return token

    async def consume_handoff(self, token_hash: str) -> HandoffState | None:
        return self.state.handoffs.pop(token_hash, None)

    async def claim_inbound(self, sid_hash: str, example_hash: str, recipient_hash: str) -> QuotaClaim:
        if sid_hash in self.state.sid_states:
            return QuotaClaim.DUPLICATE
        key = (example_hash, recipient_hash)
        if self.state.accepted.get(key, 0) + self.state.pending.get(key, 0) >= QUOTA_LIMIT:
            self.state.sid_states[sid_hash] = "quota_exhausted"
            return QuotaClaim.EXHAUSTED
        self.state.sid_states[sid_hash] = "pending"
        self.state.pending[key] = self.state.pending.get(key, 0) + 1
        return QuotaClaim.CLAIMED

    async def finalize_accepted(self, sid_hash: str, example_hash: str, recipient_hash: str) -> int:
        key = (example_hash, recipient_hash)
        self.state.pending[key] -= 1
        self.state.accepted[key] = self.state.accepted.get(key, 0) + 1
        self.state.sid_states[sid_hash] = "accepted"
        return self.state.accepted[key]

    async def finalize_failed(self, sid_hash: str, example_hash: str, recipient_hash: str) -> None:
        key = (example_hash, recipient_hash)
        self.state.pending[key] -= 1
        self.state.sid_states[sid_hash] = "failed"

    async def audit(self, delivery_hash: str, fields: dict[str, str]) -> None:
        self.audits.append((delivery_hash, dict(fields)))


class GuardService:
    called = False

    async def prepare(self, *_args: object) -> tuple[str, str]:
        self.called = True
        return "https://wa.me/example", "hash"


class FakeClient:
    def __init__(self, **_kwargs: object) -> None:
        self.closed = False
    async def post(self, *_args: object, **_kwargs: object) -> httpx.Response:
        return httpx.Response(201, json={"sid": "SM" + "1" * 32})
    async def aclose(self) -> None:
        self.closed = True


def make_service(state: SharedState | None = None, transport_state: TransportState = TransportState.ACCEPTED):
    store = MemoryStore(state)
    transport = FakeTwilioWhatsAppTransport(transport_state)
    return CustomerInitiatedWhatsAppService(store, transport, SENDER, BASE_URL), store, transport


def prepare(service: CustomerInitiatedWhatsAppService, example_id: str = REFERENCE_1_EXAMPLE_ID) -> str:
    url, _ = asyncio.run(service.prepare("Oleh", "client", example_id))
    body = parse_qs(urlparse(url).query)["text"][0]
    token = parse_trigger(body)
    assert token is not None
    return token


def inbound(token: str, number: int = 1, recipient: str = RECIPIENT) -> dict[str, str]:
    return {"Body": f"Start SiteFormo WhatsApp example {token}", "From": f"whatsapp:{recipient}",
            "To": f"whatsapp:{SENDER}", "MessageSid": "SM" + f"{number:032x}"}


def signature(url: str, params: dict[str, str], token: str) -> str:
    material = url + "".join(key + params[key] for key in sorted(params))
    return base64.b64encode(hmac.new(token.encode(), material.encode(), hashlib.sha1).digest()).decode()


def teardown_function() -> None:
    asyncio.run(api.close_whatsapp_runtime())
    api._service_override = None


def test_six_trusted_identities_and_ref01_exclusive_origin() -> None:
    assert len(CANONICAL_EXAMPLE_IDS) == 6
    assert REFERENCE_1_EXAMPLE_ID == "SF_REF_01_VELAIRE"


def test_disabled_configuration_creates_no_http_client() -> None:
    created: list[FakeClient] = []
    assert api.configure_whatsapp_runtime({}, lambda **kw: created.append(FakeClient(**kw))) is False
    assert created == [] and api._whatsapp_service is None


def test_public_ref01_prepare_resolves_trusted_scope_without_provider_call(monkeypatch) -> None:
    monkeypatch.setenv("SF_CONTACT_WHATSAPP_PUBLIC_DEMO_ENABLED", "true")
    service, _, transport = make_service()
    api._service_override = service
    with TestClient(app) as client:
        response = client.post("/api/v1/demo-contact/whatsapp", headers={"Origin": REF_ORIGIN},
                               json={"first_name": "Oleh", "example_id": REFERENCE_1_EXAMPLE_ID})
    assert response.status_code == 202 and response.json()["url"].startswith("https://wa.me/")
    assert transport.calls == []


def test_prepare_rejects_legacy_outbound_recipient_fields(monkeypatch) -> None:
    monkeypatch.setenv("SF_CONTACT_WHATSAPP_PUBLIC_DEMO_ENABLED", "true")
    service, _, transport = make_service()
    api._service_override = service
    with TestClient(app) as client:
        response = client.post("/api/v1/demo-contact/whatsapp", headers={"Origin": REF_ORIGIN},
                               json={"example_id": REFERENCE_1_EXAMPLE_ID, "phone": RECIPIENT})
    assert response.status_code == 422 and transport.calls == []


def test_cross_example_and_unknown_claim_rejected_before_service_or_redis(monkeypatch) -> None:
    monkeypatch.setenv("SF_CONTACT_WHATSAPP_PUBLIC_DEMO_ENABLED", "true")
    guard = GuardService()
    api._service_override = guard  # type: ignore[assignment]
    with TestClient(app) as client:
        cross = client.post("/api/v1/demo-contact/whatsapp", headers={"Origin": REF_ORIGIN},
                            json={"example_id": "SF_BU_01_CANONICAL_CONSULTING_EXAMPLE_V1"})
        unknown = client.post("/api/v1/demo-contact/whatsapp", headers={"Origin": DEV_ORIGIN},
                              json={"example_id": "UNKNOWN"})
    assert cross.status_code == unknown.status_code == 403
    assert guard.called is False


def test_prepare_token_contains_no_protected_state_and_is_nx_ttl_design() -> None:
    service, store, transport = make_service()
    url, correlation = asyncio.run(service.prepare("Oleh", "client", REFERENCE_1_EXAMPLE_ID))
    message = parse_qs(urlparse(url).query)["text"][0]
    token = parse_trigger(message)
    assert token and len(token) == 22 and correlation == digest(token)
    assert "SF_REF" not in token and RECIPIENT not in token and len(store.state.handoffs) == 1
    assert transport.calls == [] and HANDOFF_TTL_SECONDS == 900


def test_redis_handoff_record_uses_nx_and_short_ttl(monkeypatch) -> None:
    calls: list[tuple[str, str, dict[str, object]]] = []

    class FakeRedis:
        async def incr(self, _key: str) -> int: return 1
        async def expire(self, _key: str, _ttl: int) -> None: return None
        async def set(self, key: str, value: str, **kwargs: object) -> bool:
            calls.append((key, value, kwargs)); return True
        async def aclose(self) -> None: return None

    store = RedisWhatsAppExampleStore("redis://unused")
    monkeypatch.setattr(store, "client", lambda: FakeRedis())
    token = asyncio.run(store.create_handoff("c" * 64, "e" * 64, "Oleh"))
    assert len(token) == 22 and calls[0][0] == f"{NAMESPACE}:handoff:{digest(token)}"
    assert calls[0][2] == {"ex": HANDOFF_TTL_SECONDS, "nx": True}


def test_handoff_is_single_use_and_missing_invalid_replayed_make_zero_provider_calls() -> None:
    service, _, transport = make_service()
    token = prepare(service)
    first = asyncio.run(service.handle_inbound(inbound(token, 1)))
    replay = asyncio.run(service.handle_inbound(inbound(token, 2)))
    invalid = asyncio.run(service.handle_inbound(inbound("bad", 3)))
    assert first.outcome == "ACCEPTED" and replay.outcome == "REJECTED_HANDOFF"
    assert invalid.outcome == "IGNORED_UNAPPROVED_TRIGGER" and len(transport.calls) == 1


def test_actions_one_two_accepted_third_exhausted_and_no_day_bucket() -> None:
    state = SharedState()
    service, _, transport = make_service(state)
    results = []
    for number in range(1, 4):
        results.append(asyncio.run(service.handle_inbound(inbound(prepare(service), number))).outcome)
    assert results == ["ACCEPTED", "ACCEPTED", "QUOTA_EXHAUSTED"]
    assert len(transport.calls) == 2
    key = RedisWhatsAppExampleStore("redis://unused").quota_key(digest(REFERENCE_1_EXAMPLE_ID), digest(RECIPIENT))
    assert key == f"{NAMESPACE}:quota:WHATSAPP:{digest(REFERENCE_1_EXAMPLE_ID)}:{digest(RECIPIENT)}"
    assert ":v1:" not in key and "86400" not in key
    assert "EXPIRE',KEYS[2]" not in FINALIZE_ACCEPTED_SCRIPT


def test_email_and_whatsapp_quota_namespaces_are_independent() -> None:
    whatsapp = RedisWhatsAppExampleStore("redis://unused").quota_key("example", "contact")
    email = "sf:demo-email:v1:quota:EMAIL:example:contact"
    assert whatsapp != email and ":WHATSAPP:" in whatsapp and ":EMAIL:" in email


def test_permanent_quota_survives_service_restart_and_same_identity_other_example_is_independent() -> None:
    state = SharedState()
    first, _, _ = make_service(state)
    for number in (1, 2):
        asyncio.run(first.handle_inbound(inbound(prepare(first), number)))
    restarted, _, restarted_transport = make_service(state)
    exhausted = asyncio.run(restarted.handle_inbound(inbound(prepare(restarted), 3)))
    other = asyncio.run(restarted.handle_inbound(
        inbound(prepare(restarted, "SF_BU_01_CANONICAL_CONSULTING_EXAMPLE_V1"), 4)))
    assert exhausted.outcome == "QUOTA_EXHAUSTED" and other.outcome == "ACCEPTED"
    assert len(restarted_transport.calls) == 1


def test_provider_failure_releases_pending_claim() -> None:
    state = SharedState()
    failing, _, _ = make_service(state, TransportState.REJECTED)
    failed = asyncio.run(failing.handle_inbound(inbound(prepare(failing), 1)))
    succeeding, _, transport = make_service(state)
    accepted = asyncio.run(succeeding.handle_inbound(inbound(prepare(succeeding), 2)))
    assert failed.outcome == "FAILED" and accepted.outcome == "ACCEPTED" and len(transport.calls) == 1


def test_message_sid_replay_with_fresh_handoff_does_not_double_consume() -> None:
    service, _, transport = make_service()
    first = asyncio.run(service.handle_inbound(inbound(prepare(service), 1)))
    replay = asyncio.run(service.handle_inbound(inbound(prepare(service), 1)))
    assert first.outcome == "ACCEPTED" and replay.outcome == "DUPLICATE" and len(transport.calls) == 1


def test_sender_binding_failure_zero_provider_calls() -> None:
    service, _, transport = make_service()
    params = inbound(prepare(service)) | {"To": "whatsapp:+353800000099"}
    assert asyncio.run(service.handle_inbound(params)).outcome == "REJECTED_SENDER_BINDING"
    assert transport.calls == []


def test_invalid_twilio_signature_zero_provider_calls(monkeypatch) -> None:
    monkeypatch.setenv("SF_CONTACT_WHATSAPP_PUBLIC_DEMO_ENABLED", "true")
    monkeypatch.setenv("WHATSAPP_TWILIO_AUTH_TOKEN", "offline-auth-token")
    monkeypatch.setenv("PUBLIC_BASE_URL", BASE_URL)
    service, _, transport = make_service()
    params = inbound(prepare(service))
    api._service_override = service
    with TestClient(app) as client:
        response = client.post("/twilio/webhook", data=params, headers={"X-Twilio-Signature": "invalid"})
    assert response.status_code == 403 and transport.calls == []


def test_valid_signature_dispatches_once(monkeypatch) -> None:
    monkeypatch.setenv("SF_CONTACT_WHATSAPP_PUBLIC_DEMO_ENABLED", "true")
    monkeypatch.setenv("WHATSAPP_TWILIO_AUTH_TOKEN", "offline-auth-token")
    monkeypatch.setenv("PUBLIC_BASE_URL", BASE_URL)
    service, _, transport = make_service()
    params = inbound(prepare(service))
    api._service_override = service
    signed = signature(BASE_URL + "/twilio/webhook", params, "offline-auth-token")
    with TestClient(app) as client:
        response = client.post("/twilio/webhook", data=params, headers={"X-Twilio-Signature": signed})
    assert response.status_code == 200 and len(transport.calls) == 1


def test_audit_and_handoff_never_store_raw_protected_values() -> None:
    service, store, _ = make_service()
    result = asyncio.run(service.handle_inbound(inbound(prepare(service), 1)))
    text = repr((store.state, store.audits))
    assert result.outcome == "ACCEPTED"
    assert RECIPIENT not in text and REFERENCE_1_EXAMPLE_ID not in text and "SM000" not in text
    assert render_session_reply("Oleh") not in text
