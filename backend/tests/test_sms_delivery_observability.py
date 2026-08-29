from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
from urllib.parse import parse_qs

import httpx
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import demo_sms as api
from app.services.delivery.contracts import Claim, ClaimKind
from app.services.sms_delivery.audit import APPLY_PROVIDER_STATUS_SCRIPT
from app.services.sms_delivery.configuration import SmsConfiguration
from app.services.sms_delivery.service import SmsDeliveryService
from app.services.sms_delivery.transport import SmsTransportOutcome, TwilioSmsTransport


ACCOUNT = "AC" + "1" * 32
MESSAGE_SID = "SM" + "9" * 32
TOKEN = "offline-token"
CALLBACK_URL = "https://example.invalid/api/demo/sms/status"


class State:
    def __init__(self) -> None:
        self.status = ""
        self.quota = 0

    async def claim(self, identity):
        return Claim(ClaimKind.ACQUIRED)

    async def accept(self, identity, provider_message_id):
        self.status = "accepted"
        self.quota += 1
        return 1

    async def release(self, identity, failure_code):
        self.status = "failed"

    async def quarantine(self, identity, failure_code):
        self.status = "quarantined"


class Audit:
    def __init__(self) -> None:
        self.records = {}

    async def create(self, delivery_id, values, ttl_seconds):
        self.records[delivery_id] = dict(values)

    async def finalize(self, delivery_id, values):
        self.records[delivery_id].update(values)


class CallbackAudit:
    TERMINAL = {"delivered", "failed", "undelivered"}
    RANK = {"accepted": 1, "queued": 1, "sending": 2, "sent": 3}

    def __init__(self, current: str = "sent") -> None:
        self.current = current
        self.final_state = "PROVIDER_ACCEPTED"
        self.error_code = ""
        self.error_message = ""
        self.quota = 1

    async def apply_provider_status(self, sid_hash, status, error_code, error_message, updated_at):
        if sid_hash != hashlib.sha256(MESSAGE_SID.encode()).hexdigest():
            return False
        if self.current in self.TERMINAL:
            return True
        if status not in self.TERMINAL and self.RANK.get(status, 0) < self.RANK.get(self.current, 0):
            return True
        self.current = status
        self.final_state = {
            "delivered": "DELIVERED",
            "failed": "FAILED",
            "undelivered": "UNDELIVERED",
        }.get(status, "PROVIDER_ACCEPTED")
        self.error_code, self.error_message = error_code, error_message
        return True


def config() -> SmsConfiguration:
    return SmsConfiguration(
        True, ACCOUNT, TOKEN, "+12025550123", frozenset({"US"}), 604800,
        public_base_url="https://example.invalid",
    )


def signature(params: dict[str, str]) -> str:
    material = CALLBACK_URL + "".join(key + params[key] for key in sorted(params))
    return base64.b64encode(hmac.new(TOKEN.encode(), material.encode(), hashlib.sha1).digest()).decode()


def callback(audit: CallbackAudit, status: str, **errors: str):
    application = FastAPI()
    application.include_router(api.router)
    api._sms_configuration, api._sms_audit = config(), audit
    params = {"MessageSid": MESSAGE_SID, "MessageStatus": status, **errors}
    with TestClient(application) as client:
        return client.post(
            "/api/demo/sms/status",
            data=params,
            headers={"X-Twilio-Signature": signature(params)},
        )


def test_create_posts_status_callback_and_records_non_final_queued_state() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(201, json={"sid": MESSAGE_SID, "status": "queued"})

    async def exercise():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            transport = TwilioSmsTransport(
                account_sid=ACCOUNT, auth_token=TOKEN, sender_e164="+12025550123",
                status_callback_url=CALLBACK_URL, client=client,
            )
            state, audit = State(), Audit()
            service = SmsDeliveryService(config=config(), state=state, audit=audit, transport=transport)
            result = await service.send(
                example_id="SF_BU_04_NEXORA_EXAMPLE_V1", phone="+12025550124",
                message="Hello", first_name="Oleh", idempotency_key="observability-key-0001",
                client_id="client",
            )
            return result, state, audit

    result, state, audit = asyncio.run(exercise())
    form = parse_qs(requests[0].content.decode())
    record = next(iter(audit.records.values()))
    assert form["StatusCallback"] == [CALLBACK_URL]
    assert result.outcome is SmsTransportOutcome.ACCEPTED and state.quota == 1
    assert record["provider_status"] == "queued"
    assert record["final_state"] == "PROVIDER_ACCEPTED"
    assert record["final_state"] != "DELIVERED"


def test_create_sent_is_still_non_final() -> None:
    audit = Audit()
    state = State()

    class Transport:
        async def send(self, message, correlation_key):
            from app.services.sms_delivery.transport import SmsTransportResult
            return SmsTransportResult(
                SmsTransportOutcome.ACCEPTED, MESSAGE_SID, 201, provider_status="sent",
            )

    service = SmsDeliveryService(config=config(), state=state, audit=audit, transport=Transport())
    asyncio.run(service.send(
        example_id="SF_BU_04_NEXORA_EXAMPLE_V1", phone="+12025550124", message="Hello",
        first_name="Oleh", idempotency_key="observability-key-0002", client_id="client",
    ))
    record = next(iter(audit.records.values()))
    assert record["provider_status"] == "sent" and record["final_state"] == "PROVIDER_ACCEPTED"


def test_sent_to_delivered_is_terminal_idempotent_and_quota_neutral() -> None:
    audit = CallbackAudit()
    assert callback(audit, "delivered").status_code == 204
    assert callback(audit, "delivered").status_code == 204
    assert callback(audit, "sent").status_code == 204
    assert audit.current == "delivered" and audit.final_state == "DELIVERED"
    assert audit.quota == 1


def test_sent_to_undelivered_retains_provider_error_and_is_terminal() -> None:
    audit = CallbackAudit()
    params = {"ErrorCode": "30007", "ErrorMessage": "Message filtered"}
    assert callback(audit, "undelivered", **params).status_code == 204
    assert callback(audit, "sending").status_code == 204
    assert (audit.current, audit.final_state) == ("undelivered", "UNDELIVERED")
    assert (audit.error_code, audit.error_message) == ("30007", "Message filtered")
    assert audit.quota == 1


def test_sent_to_failed_retains_provider_error() -> None:
    audit = CallbackAudit()
    params = {"ErrorCode": "30005", "ErrorMessage": "Unknown destination handset"}
    assert callback(audit, "failed", **params).status_code == 204
    assert (audit.current, audit.final_state) == ("failed", "FAILED")
    assert (audit.error_code, audit.error_message) == ("30005", "Unknown destination handset")


def test_callback_rejects_bad_signature_and_unknown_sid_does_not_mutate() -> None:
    audit = CallbackAudit()
    application = FastAPI()
    application.include_router(api.router)
    api._sms_configuration, api._sms_audit = config(), audit
    with TestClient(application) as client:
        bad = client.post(
            "/api/demo/sms/status",
            data={"MessageSid": MESSAGE_SID, "MessageStatus": "delivered"},
            headers={"X-Twilio-Signature": "bad"},
        )
    assert bad.status_code == 403 and audit.current == "sent" and audit.quota == 1
    unknown_params = {"MessageSid": "SM" + "8" * 32, "MessageStatus": "delivered"}
    with TestClient(application) as client:
        unknown = client.post(
            "/api/demo/sms/status",
            data=unknown_params,
            headers={"X-Twilio-Signature": signature(unknown_params)},
        )
    assert unknown.status_code == 409 and audit.current == "sent" and audit.quota == 1


def test_redis_transition_is_atomic_and_guards_terminal_regression() -> None:
    assert "local terminal" in APPLY_PROVIDER_STATUS_SCRIPT
    assert "return {'TERMINAL'}" in APPLY_PROVIDER_STATUS_SCRIPT
    assert "return {'STALE'}" in APPLY_PROVIDER_STATUS_SCRIPT
    assert "final_state = 'DELIVERED'" in APPLY_PROVIDER_STATUS_SCRIPT
    assert "final_state = 'FAILED'" in APPLY_PROVIDER_STATUS_SCRIPT
    assert "final_state = 'UNDELIVERED'" in APPLY_PROVIDER_STATUS_SCRIPT
