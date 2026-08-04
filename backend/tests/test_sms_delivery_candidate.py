from __future__ import annotations

import asyncio
import json
import socket
from collections.abc import Iterator

import httpx
import pytest

from app.services.delivery.contracts import Claim, ClaimKind, DeliveryIdentity
from app.services.sms_delivery.configuration import SmsConfiguration, resolve_sms_configuration
from app.services.sms_delivery.contract import SmsDemoRequest
from app.services.sms_delivery.models import render_balanced_message, validate_destination
from app.services.sms_delivery.service import SmsDeliveryError, SmsDeliveryService
from app.services.sms_delivery.transport import SmsTransportOutcome, SmsTransportResult, TwilioSmsTransport


class MemoryState:
    def __init__(self) -> None:
        self.records: dict[str, tuple[DeliveryIdentity, str, str | None]] = {}
        self.lock = asyncio.Lock()

    async def claim(self, identity: DeliveryIdentity) -> Claim:
        async with self.lock:
            prior = self.records.get(identity.idempotency_hash)
            if prior:
                stored, status, value = prior
                if stored.fingerprint != identity.fingerprint:
                    return Claim(ClaimKind.CONFLICT)
                if status == "accepted":
                    return Claim(ClaimKind.REPLAY_ACCEPTED, value)
                if status == "pending":
                    return Claim(ClaimKind.IN_PROGRESS)
                if status == "quarantined":
                    return Claim(ClaimKind.REPLAY_QUARANTINED)
            self.records[identity.idempotency_hash] = (identity, "pending", None)
            return Claim(ClaimKind.ACQUIRED)

    async def accept(self, identity: DeliveryIdentity, provider_message_id: str) -> int:
        self.records[identity.idempotency_hash] = (identity, "accepted", provider_message_id)
        return 0

    async def release(self, identity: DeliveryIdentity, failure_code: str) -> None:
        self.records[identity.idempotency_hash] = (identity, "failed", failure_code)

    async def quarantine(self, identity: DeliveryIdentity, failure_code: str) -> None:
        self.records[identity.idempotency_hash] = (identity, "quarantined", failure_code)


class MemoryAudit:
    def __init__(self, fail_create: bool = False, fail_finalize: bool = False) -> None:
        self.records: dict[str, dict[str, str]] = {}
        self.fail_create, self.fail_finalize = fail_create, fail_finalize

    async def create(self, delivery_id: str, values: dict[str, str], ttl_seconds: int) -> None:
        if self.fail_create:
            raise RuntimeError("offline audit failure")
        self.records[delivery_id] = {"delivery_id": delivery_id, **values, "ttl": str(ttl_seconds)}

    async def finalize(self, delivery_id: str, values: dict[str, str]) -> None:
        if self.fail_finalize:
            raise RuntimeError("offline audit failure")
        self.records[delivery_id].update(values)


class FakeTransport:
    def __init__(self, result: SmsTransportResult | None = None) -> None:
        self.result = result or SmsTransportResult(SmsTransportOutcome.ACCEPTED, "SM" + "0" * 32, 201)
        self.calls = []

    async def send(self, message, correlation_key):
        self.calls.append((message, correlation_key))
        return self.result


def ready_config(enabled: bool = True) -> SmsConfiguration:
    return SmsConfiguration(enabled, "AC" + "1" * 32, "not-a-real-secret", "+12025550123", frozenset({"US"}), 604800)


def make_service(*, outcome: SmsTransportOutcome = SmsTransportOutcome.ACCEPTED, audit=None):
    state = MemoryState()
    sid = "SM" + "0" * 32 if outcome is SmsTransportOutcome.ACCEPTED else None
    transport = FakeTransport(SmsTransportResult(outcome, sid, 201 if outcome in {SmsTransportOutcome.ACCEPTED, SmsTransportOutcome.AMBIGUOUS} else None))
    audit = audit or MemoryAudit()
    return SmsDeliveryService(config=ready_config(), state=state, audit=audit, transport=transport), state, audit, transport


async def send(service: SmsDeliveryService, key: str = "sms-idempotency-0001", **changes):
    values = {"example_id": "TRUSTED_EXAMPLE", "phone": "+12025550124", "message": "Please confirm my enquiry.", "first_name": "Oleh", "idempotency_key": key, "client_id": "privacy-safe-client"}
    values.update(changes)
    return await service.send(**values)


@pytest.fixture(autouse=True)
def deny_network(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    def blocked(*args, **kwargs):
        raise AssertionError("external network denied in SMS Stage 02 tests")
    monkeypatch.setattr(socket, "create_connection", blocked)
    yield


def test_contract_excludes_server_authority_and_provider_fields() -> None:
    assert set(SmsDemoRequest.model_fields) == {"first_name", "phone", "message", "idempotency_key"}
    with pytest.raises(Exception):
        SmsDemoRequest(first_name="Oleh", phone="+12025550124", idempotency_key="sms-idempotency-0001", example_context="attacker")


def test_valid_and_invalid_e164_and_country_allowlist() -> None:
    assert validate_destination("+12025550124", frozenset({"US"})) == "+12025550124"
    for value in ("2025550124", "+1 202 555 0124", "+123"):
        with pytest.raises(ValueError, match="invalid_e164_phone"):
            validate_destination(value, frozenset({"US"}))
    with pytest.raises(ValueError, match="sms_country_not_allowed"):
        validate_destination("+447700900123", frozenset({"US"}))
    with pytest.raises(ValueError, match="sms_premium_destination_blocked"):
        validate_destination("+19005550123", frozenset({"US"}))


def test_message_candidates_balanced_named_and_neutral() -> None:
    assert render_balanced_message("Oleh") == (
        "Hello, Oleh.\n\n"
        "This is an example of a short SMS notification your future website could send "
        "to your customers.\n\n"
        "SiteFormo"
    )
    assert render_balanced_message("") == (
        "Hello.\n\n"
        "This is an example of a short SMS notification your future website could send "
        "to your customers.\n\n"
        "SiteFormo"
    )
    assert len(render_balanced_message("Oleh")) <= 160


def test_configuration_is_sms_only_missing_and_disabled() -> None:
    config = resolve_sms_configuration({"TWILIO_WHATSAPP_FROM": "+12025550123"})
    assert config.enabled is False and config.sender_e164 is None
    with pytest.raises(ValueError, match="sms_demo_disabled"):
        config.require_ready()
    with pytest.raises(ValueError, match="sms_provider_not_configured"):
        SmsConfiguration(True, allowed_countries=frozenset({"US"})).require_ready()


def test_disabled_service_produces_zero_http_calls() -> None:
    state, audit, transport = MemoryState(), MemoryAudit(), FakeTransport()
    service = SmsDeliveryService(config=ready_config(False), state=state, audit=audit, transport=transport)
    with pytest.raises(SmsDeliveryError, match="sms_demo_disabled"):
        asyncio.run(send(service))
    assert not state.records and not audit.records and not transport.calls


@pytest.mark.parametrize("outcome", [SmsTransportOutcome.AMBIGUOUS, SmsTransportOutcome.REJECTED, SmsTransportOutcome.QUARANTINED])
def test_typed_failures_no_retry(outcome: SmsTransportOutcome) -> None:
    service, state, audit, transport = make_service(outcome=outcome)
    with pytest.raises(SmsDeliveryError):
        asyncio.run(send(service))
    assert len(transport.calls) == 1
    record = next(iter(audit.records.values()))
    assert record["typed_outcome"] == outcome.value
    assert next(iter(state.records.values()))[1] == ("quarantined" if outcome in {SmsTransportOutcome.AMBIGUOUS, SmsTransportOutcome.QUARANTINED} else "failed")


def test_exactly_one_provider_call_idempotent_replay_and_hash_only_audit() -> None:
    service, _, audit, transport = make_service()
    assert asyncio.run(send(service)).outcome is SmsTransportOutcome.ACCEPTED
    assert asyncio.run(send(service)).outcome is SmsTransportOutcome.ACCEPTED
    assert len(transport.calls) == 1
    serialized = json.dumps(audit.records)
    assert "+12025550124" not in serialized
    assert "sms-idempotency-0001" not in serialized
    assert "SM" + "0" * 32 not in serialized
    assert "not-a-real-secret" not in serialized
    record = next(iter(audit.records.values()))
    assert record["provider_call_count"] == "1" and record["message_sid_present"] == "true"


def test_concurrent_idempotency_one_winner() -> None:
    service, _, _, transport = make_service()
    async def exercise():
        return await asyncio.gather(send(service), send(service), return_exceptions=True)
    results = asyncio.run(exercise())
    assert len(transport.calls) == 1
    assert [result.outcome for result in results] == [SmsTransportOutcome.ACCEPTED, SmsTransportOutcome.ACCEPTED]
    assert sorted(result.replayed for result in results) == [False, True]


def test_audit_failure_is_fail_closed_before_provider() -> None:
    audit = MemoryAudit(fail_create=True)
    service, _, _, transport = make_service(audit=audit)
    with pytest.raises(SmsDeliveryError, match="sms_audit_unavailable"):
        asyncio.run(send(service))
    assert transport.calls == []


@pytest.mark.parametrize(
    ("status", "body", "expected"),
    [(201, {"sid": "SM" + "a" * 32}, SmsTransportOutcome.ACCEPTED), (200, {}, SmsTransportOutcome.AMBIGUOUS), (400, {"code": 21211}, SmsTransportOutcome.REJECTED)],
)
def test_twilio_response_mapping_offline(status, body, expected) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json=body)
    async def exercise():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            transport = TwilioSmsTransport(account_sid="AC" + "1" * 32, auth_token="secret", sender_e164="+12025550123", client=client)
            return await transport.send(type("Message", (), {"destination_e164": "+12025550124", "body": "server-owned"})(), "key")
    assert asyncio.run(exercise()).outcome is expected


def test_timeout_is_quarantined_without_retry() -> None:
    calls = 0
    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        raise httpx.ReadTimeout("offline timeout", request=request)
    async def exercise():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            transport = TwilioSmsTransport(account_sid="AC" + "1" * 32, auth_token="secret", sender_e164="+12025550123", client=client)
            return await transport.send(type("Message", (), {"destination_e164": "+12025550124", "body": "server-owned"})(), "key")
    assert asyncio.run(exercise()).outcome is SmsTransportOutcome.QUARANTINED
    assert calls == 1
