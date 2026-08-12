from __future__ import annotations

import asyncio
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.api import demo_sms as api
from app.main import app
from app.services.delivery.contracts import Claim, ClaimKind, DeliveryIdentity
from app.services.sms_delivery.configuration import SmsConfiguration
from app.services.sms_delivery.service import SmsDeliveryService
from app.services.sms_delivery.transport import SmsTransportOutcome, SmsTransportResult
from scripts.dry_run_sms_demo import build_dry_run

ORIGIN = "https://dev.siteformo.com"


class RuntimeState:
    def __init__(self, limit: int = 2) -> None:
        self.limit = limit
        self.records: dict[str, tuple[DeliveryIdentity, str, str | None]] = {}
        self.lock = asyncio.Lock()

    async def claim(self, identity: DeliveryIdentity) -> Claim:
        async with self.lock:
            prior = self.records.get(identity.idempotency_hash)
            if prior:
                stored, state, value = prior
                if stored.fingerprint != identity.fingerprint:
                    return Claim(ClaimKind.CONFLICT)
                if state == "accepted":
                    return Claim(ClaimKind.REPLAY_ACCEPTED, value)
                return Claim(ClaimKind.IN_PROGRESS)
            used = sum(item.recipient_hash == identity.recipient_hash and state in {"pending", "accepted", "quarantined"} for item, state, _ in self.records.values())
            if used >= self.limit:
                return Claim(ClaimKind.QUOTA_EXHAUSTED)
            self.records[identity.idempotency_hash] = (identity, "pending", None)
            return Claim(ClaimKind.ACQUIRED)

    async def accept(self, identity: DeliveryIdentity, provider_message_id: str) -> int:
        self.records[identity.idempotency_hash] = (identity, "accepted", provider_message_id)
        return 0

    async def release(self, identity: DeliveryIdentity, failure_code: str) -> None:
        self.records[identity.idempotency_hash] = (identity, "failed", failure_code)

    async def quarantine(self, identity: DeliveryIdentity, failure_code: str) -> None:
        self.records[identity.idempotency_hash] = (identity, "quarantined", failure_code)


class RuntimeAudit:
    def __init__(self) -> None:
        self.records = {}

    async def create(self, delivery_id, values, ttl_seconds):
        self.records[delivery_id] = {**values, "ttl": str(ttl_seconds)}

    async def finalize(self, delivery_id, values):
        self.records[delivery_id].update(values)


class RuntimeTransport:
    def __init__(self, outcome=SmsTransportOutcome.ACCEPTED) -> None:
        self.calls = []
        self.outcome = outcome

    async def send(self, message, correlation_key):
        self.calls.append((message, correlation_key))
        sid = "SM" + "1" * 32 if self.outcome is SmsTransportOutcome.ACCEPTED else None
        return SmsTransportResult(self.outcome, sid, 201 if self.outcome is SmsTransportOutcome.ACCEPTED else 400)


def runtime_service(outcome=SmsTransportOutcome.ACCEPTED):
    state, audit, transport = RuntimeState(), RuntimeAudit(), RuntimeTransport(outcome)
    config = SmsConfiguration(True, "AC" + "1" * 32, "synthetic", "+12025550123", frozenset({"US"}), 604800)
    return SmsDeliveryService(config=config, state=state, audit=audit, transport=transport), state, audit, transport


def payload(key="sms-runtime-key-0001", phone="+12025550124"):
    return {"first_name": "Oleh", "phone": phone, "customer_message": "Hi SiteFormo", "idempotency_key": key}


@pytest.fixture(autouse=True)
def restore_runtime(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("SMS_DEMO_ENABLED", "false")
    api._sms_service = None
    yield
    api._sms_service = None


def test_disabled_endpoint_and_factory_create_zero_clients_and_calls() -> None:
    created = []
    assert api.configure_sms_runtime({}, lambda **kwargs: created.append(kwargs)) is False
    with TestClient(app) as client:
        response = client.post("/api/demo/sms/start", headers={"Origin": ORIGIN}, json=payload())
    assert response.status_code == 503 and response.json() == {"detail": "sms_demo_disabled"}
    assert response.headers.get("cache-control") == "no-store"
    assert created == []


def test_enabled_missing_env_creates_no_http_client() -> None:
    created = []
    assert api.configure_sms_runtime({"SMS_DEMO_ENABLED": "true"}, lambda **kwargs: created.append(kwargs)) is False
    assert created == [] and api._sms_service is None


def test_missing_configuration_endpoint_fails_closed_with_no_store(monkeypatch: pytest.MonkeyPatch) -> None:
    created = []
    monkeypatch.setenv("SMS_DEMO_ENABLED", "true")
    assert api.configure_sms_runtime({"SMS_DEMO_ENABLED": "true"}, lambda **kwargs: created.append(kwargs)) is False
    with TestClient(app) as client:
        response = client.post("/api/demo/sms/start", headers={"Origin": ORIGIN}, json=payload())
    assert response.status_code == 503 and response.json() == {"detail": "sms_runtime_unavailable"}
    assert response.headers.get("cache-control") == "no-store"
    assert created == [] and api._sms_service is None


def test_invalid_request_has_no_store_and_zero_provider_calls() -> None:
    with TestClient(app) as client:
        response = client.post("/api/demo/sms/start", headers={"Origin": ORIGIN}, json={})
    assert response.status_code == 422
    assert response.headers.get("cache-control") == "no-store"
    assert api._sms_service is None


def test_exact_origin_and_no_store(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SMS_DEMO_ENABLED", "true")
    service, _, _, transport = runtime_service()
    api._sms_service = service
    with TestClient(app) as client:
        forbidden = client.post("/api/demo/sms/start", headers={"Origin": ORIGIN + ".evil"}, json=payload())
        api._sms_service = service
        accepted = client.post("/api/demo/sms/start", headers={"Origin": ORIGIN}, json=payload())
    assert forbidden.status_code == 403
    assert accepted.status_code == 201 and accepted.headers["cache-control"] == "no-store"
    assert accepted.json()["status"] == "accepted"
    assert len(transport.calls) == 1
    assert transport.calls[0][0].body == (
        'Hi Oleh.\n\nYour message:\n\n"Hi SiteFormo"\n\nThis is an example of how your '
        "customers can start an SMS conversation from your future website.\n\nSiteFormo"
    )


@pytest.mark.parametrize(("phone", "detail"), [("2025550124", "invalid_e164_phone"), ("+447700900123", "sms_country_not_allowed")])
def test_phone_fail_closed_before_transport(monkeypatch: pytest.MonkeyPatch, phone: str, detail: str) -> None:
    monkeypatch.setenv("SMS_DEMO_ENABLED", "true")
    service, _, audit, transport = runtime_service()
    with TestClient(app) as client:
        api._sms_service = service
        response = client.post("/api/demo/sms/start", headers={"Origin": ORIGIN}, json=payload(phone=phone))
    assert response.status_code == 422 and response.json() == {"detail": detail}
    assert audit.records == {} and transport.calls == []
    with TestClient(app) as client:
        api._sms_service = service
        accepted = client.post("/api/demo/sms/start", headers={"Origin": ORIGIN}, json=payload())
    assert accepted.status_code == 201
    assert len(transport.calls) == 1


def test_replay_and_quota_through_runtime(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SMS_DEMO_ENABLED", "true")
    service, _, audit, transport = runtime_service()
    with TestClient(app) as client:
        api._sms_service = service
        first = client.post("/api/demo/sms/start", headers={"Origin": ORIGIN}, json=payload())
        api._sms_service = service
        replay = client.post("/api/demo/sms/start", headers={"Origin": ORIGIN}, json=payload())
        api._sms_service = service
        second = client.post("/api/demo/sms/start", headers={"Origin": ORIGIN}, json=payload("sms-runtime-key-0002"))
        api._sms_service = service
        quota = client.post("/api/demo/sms/start", headers={"Origin": ORIGIN}, json=payload("sms-runtime-key-0003"))
    assert first.status_code == replay.status_code == second.status_code == 201
    assert replay.json()["replayed"] is True
    assert quota.status_code == 429 and quota.json() == {"detail": "sms_quota_exhausted"}
    assert len(transport.calls) == 2
    assert all(record["provider_call_count"] == "1" for record in audit.records.values())


@pytest.mark.parametrize("outcome", [SmsTransportOutcome.REJECTED, SmsTransportOutcome.AMBIGUOUS, SmsTransportOutcome.QUARANTINED])
def test_runtime_typed_provider_failures_are_single_call(monkeypatch: pytest.MonkeyPatch, outcome) -> None:
    monkeypatch.setenv("SMS_DEMO_ENABLED", "true")
    service, _, audit, transport = runtime_service(outcome)
    with TestClient(app) as client:
        api._sms_service = service
        response = client.post("/api/demo/sms/start", headers={"Origin": ORIGIN}, json=payload())
    assert response.status_code == 502 and len(transport.calls) == 1
    assert next(iter(audit.records.values()))["typed_outcome"] == outcome.value
    if outcome is SmsTransportOutcome.REJECTED:
        transport.outcome = SmsTransportOutcome.ACCEPTED
        with TestClient(app) as client:
            api._sms_service = service
            accepted = client.post(
                "/api/demo/sms/start",
                headers={"Origin": ORIGIN},
                json=payload("sms-runtime-key-0002"),
            )
        assert accepted.status_code == 201
        assert len(transport.calls) == 2


def test_dry_run_is_privacy_safe_and_has_no_transport() -> None:
    result = build_dry_run(first_name="Oleh", phone="+12025550124", message="Hi SiteFormo", idempotency_key="sms-runtime-key-0001", countries=frozenset({"US"}))
    assert result["typed_outcome"] == "DRY_RUN"
    assert result["audit_candidate"]["provider_call_count"] == 0
    assert result["audit_candidate"]["transport_invoked"] is False
    assert "+12025550124" not in str(result)
    assert "Please confirm my enquiry." not in str(result)
    assert result["legs"][0]["message_encoding"] == "GSM-7"
    assert result["legs"][0]["message_segment_count"] == 1
