from __future__ import annotations

import asyncio
import hashlib

import httpx
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import demo_voice as api
from app.services.voice_delivery import runtime
from app.services.voice_delivery.configuration import VoiceConfiguration, resolve_configuration
from app.services.voice_delivery.models import (
    ProviderResult, ScheduleResult, VoiceRequest, VoiceState, normalize_name,
    validate_phone,
)
from app.services.voice_delivery.security import validate_twilio_signature
from app.services.voice_delivery.service import VoiceDemoService, VoiceDispatcher
from app.services.voice_delivery.store import CLAIM_DUE_SCRIPT, SCHEDULE_SCRIPT
from app.services.voice_delivery.transport import TwilioVoiceTransport, TwilioVoiceTransportConfig
from app.services.voice_delivery.twiml import render_twiml, spoken_script

ACCOUNT = "AC" + "1" * 32
CALL_SID = "CA" + "2" * 32
ORIGIN = "https://dev.siteformo.com"
EXAMPLE = "SF_BU_01_CANONICAL_CONSULTING_EXAMPLE_V1"


def ready_config(**changes: object) -> VoiceConfiguration:
    values = dict(
        enabled=True, account_sid=ACCOUNT, auth_token="offline-token",
        caller_e164="+17000000457", allowed_countries=frozenset({"IE"}),
        public_base_url="https://example.invalid", delay_seconds=7,
    )
    values.update(changes)
    return VoiceConfiguration(**values)


class MemoryStore:
    def __init__(self) -> None:
        self.scheduled: dict[str, tuple[VoiceRequest, str]] = {}
        self.due: list[VoiceRequest] = []
        self.finalized: list[tuple[VoiceState, str]] = []
        self.callbacks: list[tuple[str, VoiceState]] = []

    async def schedule(self, request: VoiceRequest, *, fingerprint: str) -> ScheduleResult:
        prior = self.scheduled.get(request.idempotency_hash)
        if prior:
            if prior[1] != fingerprint:
                raise ValueError("voice_idempotency_conflict")
            saved = prior[0]
            return ScheduleResult(VoiceState.DUPLICATE_SUPPRESSED, saved.request_id, saved.scheduled_at, True)
        self.scheduled[request.idempotency_hash] = (request, fingerprint)
        self.due.append(request)
        return ScheduleResult(VoiceState.DELAYED, request.request_id, request.scheduled_at)

    async def claim_due(self, now_seconds: int) -> VoiceRequest | None:
        for index, item in enumerate(self.due):
            if item.scheduled_at <= now_seconds:
                return self.due.pop(index)
        return None

    async def finalize_submission(self, request: VoiceRequest, state: VoiceState, sid_hash: str) -> None:
        self.finalized.append((state, sid_hash))

    async def apply_callback(self, sid_hash: str, state: VoiceState, timestamp: int) -> bool:
        self.callbacks.append((sid_hash, state))
        return True


class FakeTransport:
    def __init__(self, result: ProviderResult | None = None) -> None:
        self.result = result or ProviderResult(VoiceState.PROVIDER_SUBMITTED, 201, CALL_SID)
        self.calls: list[VoiceRequest] = []

    async def submit(self, request: VoiceRequest) -> ProviderResult:
        self.calls.append(request)
        return self.result


def test_exact_server_owned_script_and_terminal_twiml() -> None:
    expected = (
        "Hello, Oleh. Welcome to SiteFormo. "
        "This is how your future customers can request a phone call from your website. Thank you."
    )
    assert spoken_script(" Oleh ") == expected
    twiml = render_twiml("Oleh", voice="Polly.Amy-Neural", language="en-GB")
    assert '<Say voice="Polly.Amy-Neural" language="en-GB">' in twiml
    assert expected in twiml and "<Hangup" in twiml
    assert all(token not in twiml for token in ("<Dial", "<Gather", "<Record", "<Redirect", "<Pause"))
    assert twiml.index("</Say>") < twiml.index("<Hangup")


def test_configuration_disabled_and_incomplete_create_zero_clients() -> None:
    created: list[object] = []
    assert runtime.configure_voice_runtime({"VOICE_DEMO_ENABLED": "false"}, lambda **kw: created.append(kw)) is False
    assert runtime.configure_voice_runtime({"VOICE_DEMO_ENABLED": "true"}, lambda **kw: created.append(kw)) is False
    assert created == [] and runtime.service is None and runtime.dispatcher is None


def test_configuration_is_voice_specific_and_ie_only() -> None:
    config = resolve_configuration({
        "VOICE_DEMO_ENABLED": "true", "TWILIO_VOICE_ACCOUNT_SID": ACCOUNT,
        "TWILIO_VOICE_AUTH_TOKEN": "token", "TWILIO_VOICE_FROM": "+17000000457",
        "VOICE_DEMO_ALLOWED_COUNTRIES": "IE", "VOICE_DEMO_PUBLIC_BASE_URL": "https://example.invalid",
    })
    config.require_ready()
    assert config.allowed_countries == frozenset({"IE"}) and config.delay_seconds == 7


def test_input_and_atomic_store_safety_contracts() -> None:
    assert normalize_name("  Oleh  ") == "Oleh"
    assert validate_phone("+353871234567", frozenset({"IE"})) == "+353871234567"
    for phone in ("+17000000000", "+353151234567", "353871234567"):
        try:
            validate_phone(phone, frozenset({"IE"}))
        except ValueError:
            pass
        else:
            raise AssertionError(f"unsafe destination accepted: {phone}")
    assert "RECIPIENT_QUOTA" in SCHEDULE_SCRIPT
    assert "GLOBAL_QUOTA" in SCHEDULE_SCRIPT
    assert "RATE_LIMITED" in SCHEDULE_SCRIPT
    assert "TIMEOUT_QUARANTINED" in CLAIM_DUE_SCRIPT
    assert "HDEL', record, 'first_name', 'phone'" in CLAIM_DUE_SCRIPT


def test_request_is_delayed_deduplicated_and_conflicts_fail_closed() -> None:
    store = MemoryStore()
    service = VoiceDemoService(ready_config(), store)
    first = asyncio.run(service.request_call(example_id=EXAMPLE, first_name="Oleh", phone="+353871234567", idempotency_key="voice-key-0000001", client_id="client", now_seconds=100))
    replay = asyncio.run(service.request_call(example_id=EXAMPLE, first_name="Oleh", phone="+353871234567", idempotency_key="voice-key-0000001", client_id="client", now_seconds=101))
    assert first.scheduled_at == 107 and replay.replayed and len(store.due) == 1
    try:
        asyncio.run(service.request_call(example_id=EXAMPLE, first_name="Other", phone="+353871234567", idempotency_key="voice-key-0000001", client_id="client", now_seconds=101))
    except ValueError as exc:
        assert str(exc) == "voice_idempotency_conflict"
    else:
        raise AssertionError("conflicting replay was accepted")


def test_dispatcher_submits_at_most_once_and_timeout_is_not_retried() -> None:
    store, transport = MemoryStore(), FakeTransport(ProviderResult(VoiceState.TIMEOUT_QUARANTINED))
    service = VoiceDemoService(ready_config(), store)
    asyncio.run(service.request_call(example_id=EXAMPLE, first_name="Oleh", phone="+353871234567", idempotency_key="voice-key-0000002", client_id="client", now_seconds=100))
    dispatcher = VoiceDispatcher(store, transport)  # type: ignore[arg-type]
    assert asyncio.run(dispatcher.run_once(107)) is True
    assert asyncio.run(dispatcher.run_once(108)) is False
    assert len(transport.calls) == 1 and store.finalized == [(VoiceState.TIMEOUT_QUARANTINED, "")]


def test_transport_posts_one_server_owned_call_without_retry_features() -> None:
    captured: list[httpx.Request] = []
    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(201, json={"sid": CALL_SID})
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    config = TwilioVoiceTransportConfig(ACCOUNT, "token", "+17000000457", "https://example.invalid/api/demo/voice/status", "Polly.Amy-Neural", "en-GB")
    request = VoiceRequest("r", "e" * 32, "Oleh", "+353871234567", "a" * 64, "b" * 64, "c" * 64, 107)
    result = asyncio.run(TwilioVoiceTransport(config, client).submit(request))
    asyncio.run(client.aclose())
    body = captured[0].content.decode()
    assert result.state is VoiceState.PROVIDER_SUBMITTED and len(captured) == 1
    assert "Twiml=" in body and "StatusCallback=" in body and "To=" in body and "From=" in body
    assert all(term not in body for term in ("Record", "MachineDetection", "SendDigits"))


def test_signature_validation() -> None:
    import base64, hmac
    url, params, token = "https://example.invalid/status", {"CallSid": CALL_SID}, "secret"
    payload = url + "CallSid" + CALL_SID
    signature = base64.b64encode(hmac.new(token.encode(), payload.encode(), hashlib.sha1).digest()).decode()
    assert validate_twilio_signature(url, params, signature, token)
    assert not validate_twilio_signature(url, params, "bad", token)


def test_api_disabled_is_fail_closed_no_store() -> None:
    application = FastAPI()
    application.include_router(api.router)
    runtime.configuration, runtime.service = resolve_configuration({"VOICE_DEMO_ENABLED": "false"}), None
    with TestClient(application) as client:
        response = client.post("/api/demo/voice/request", headers={"Origin": ORIGIN}, json={
            "first_name": "Oleh", "phone": "+353871234567", "idempotency_key": "voice-key-0000003",
        })
    assert response.status_code == 503 and response.json()["detail"] == "voice_demo_disabled"
    assert response.headers["cache-control"] == "no-store"


def test_api_accepts_only_contract_fields_and_success_is_no_store() -> None:
    application, store = FastAPI(), MemoryStore()
    application.include_router(api.router)
    runtime.configuration = ready_config()
    runtime.service = VoiceDemoService(runtime.configuration, store)
    with TestClient(application) as client:
        extra = client.post("/api/demo/voice/request", headers={"Origin": ORIGIN}, json={
            "first_name": "Oleh", "phone": "+353871234567", "idempotency_key": "voice-key-0000004", "twiml": "<Dial/>",
        })
        accepted = client.post("/api/demo/voice/request", headers={"Origin": ORIGIN}, json={
            "first_name": "Oleh", "phone": "+353871234567", "idempotency_key": "voice-key-0000005",
        })
    assert extra.status_code == 422
    assert accepted.status_code == 202 and accepted.headers["cache-control"] == "no-store"
    assert len(store.due) == 1
