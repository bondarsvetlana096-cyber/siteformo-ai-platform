from __future__ import annotations

import asyncio
import json

import pytest

from app.services.delivery.contracts import Claim, ClaimKind
from app.services.sms_delivery.configuration import SmsConfiguration, resolve_sms_configuration
from app.services.sms_delivery.contract import SmsDemoRequest
from app.services.sms_delivery.models import SMSDeliveryMode, SMSDeliveryRole
from app.services.sms_delivery.service import SmsAggregateOutcome, SmsDeliveryError, SmsDeliveryService
from app.services.sms_delivery.transport import SmsTransportOutcome, SmsTransportResult
from scripts.dry_run_sms_demo import build_dry_run


class State:
    def __init__(self):
        self.records = {}

    async def claim(self, identity):
        prior = self.records.get(identity.idempotency_hash)
        if prior == "accepted":
            return Claim(ClaimKind.REPLAY_ACCEPTED, "synthetic")
        if prior == "pending":
            return Claim(ClaimKind.IN_PROGRESS)
        self.records[identity.idempotency_hash] = "pending"
        return Claim(ClaimKind.ACQUIRED)

    async def accept(self, identity, provider_message_id):
        self.records[identity.idempotency_hash] = "accepted"
        return 0

    async def release(self, identity, failure_code):
        self.records[identity.idempotency_hash] = "failed"

    async def quarantine(self, identity, failure_code):
        self.records[identity.idempotency_hash] = "quarantined"


class Audit:
    def __init__(self):
        self.records = {}

    async def create(self, delivery_id, values, ttl_seconds):
        self.records[delivery_id] = dict(values)

    async def finalize(self, delivery_id, values):
        self.records[delivery_id].update(values)


class Transport:
    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.calls = []

    async def send(self, message, correlation_key):
        self.calls.append(message)
        outcome = self.outcomes.pop(0)
        sid = "SM" + str(len(self.calls)) * 32 if outcome is SmsTransportOutcome.ACCEPTED else None
        return SmsTransportResult(outcome, sid, 201 if sid else 400)


def config(mode, **changes):
    values = dict(
        enabled=True, account_sid="AC" + "1" * 32, auth_token="synthetic",
        sender_e164="+12025550123", allowed_countries=frozenset({"US"}),
        audit_ttl_seconds=604800, delivery_mode=mode, owner_to_e164="+12025550125",
        visitor_notifications_enabled=True, owner_requires_visitor_contact=False,
    )
    values.update(changes)
    return SmsConfiguration(**values)


def exercise(mode, outcomes, **changes):
    state, audit, transport = State(), Audit(), Transport(outcomes)
    service = SmsDeliveryService(config=config(mode, **changes), state=state, audit=audit, transport=transport)
    result = asyncio.run(service.send(
        example_id="TRUSTED_EXAMPLE", phone="+12025550124", message="Call me back",
        first_name="Oleh", idempotency_key="sms-config-mode-key-0001", client_id="safe-client",
    ))
    return result, state, audit, transport


def test_public_contract_exposes_no_routing_authority() -> None:
    assert set(SmsDemoRequest.model_fields) == {"example_id", "first_name", "phone", "customer_message", "idempotency_key"}
    with pytest.raises(Exception):
        SmsDemoRequest(
            first_name="Oleh", phone="+12025550124", customer_message="Hello",
            idempotency_key="sms-config-mode-key-0001", delivery_mode="OWNER_ALERT",
            owner_phone="+12025550125",
        )


def test_mode_resolves_only_from_server_configuration() -> None:
    cfg = resolve_sms_configuration({"SMS_DELIVERY_MODE": "both"})
    assert cfg.delivery_mode is SMSDeliveryMode.BOTH
    with pytest.raises(ValueError, match="invalid_sms_delivery_mode"):
        resolve_sms_configuration({"SMS_DELIVERY_MODE": "visitor_choice"})


def test_visitor_notification_targets_only_visitor_and_replays_once() -> None:
    result, state, audit, transport = exercise(SMSDeliveryMode.VISITOR_NOTIFICATION, [SmsTransportOutcome.ACCEPTED])
    assert result.outcome is SmsTransportOutcome.ACCEPTED
    assert len(transport.calls) == 1 and transport.calls[0].destination_e164 == "+12025550124"
    assert transport.calls[0].body == (
        'Hi Oleh.\n\nYour message:\n\n"Call me back"\n\nThis is an example of how your '
        "customers can start an SMS conversation from your future website.\n\nSiteFormo"
    )
    assert next(iter(audit.records.values()))["delivery_role"] == SMSDeliveryRole.VISITOR.value


@pytest.mark.parametrize(
    ("phone", "first_name", "error"),
    [(None, "Oleh", "invalid_e164_phone"), ("+12025550124", None, "invalid_first_name")],
)
def test_visitor_required_fields_fail_before_provider(phone, first_name, error) -> None:
    state, audit, transport = State(), Audit(), Transport([SmsTransportOutcome.ACCEPTED])
    service = SmsDeliveryService(
        config=config(SMSDeliveryMode.VISITOR_NOTIFICATION), state=state, audit=audit, transport=transport
    )
    with pytest.raises(SmsDeliveryError, match=error):
        asyncio.run(service.send(
            example_id="TRUSTED_EXAMPLE", phone=phone, message="Call me back", first_name=first_name,
            idempotency_key="sms-config-mode-key-0001", client_id="safe-client",
        ))
    assert transport.calls == [] and audit.records == {}


def test_owner_alert_uses_only_server_owner_recipient() -> None:
    result, _, audit, transport = exercise(SMSDeliveryMode.OWNER_ALERT, [SmsTransportOutcome.ACCEPTED])
    assert result.outcome is SmsTransportOutcome.ACCEPTED
    assert len(transport.calls) == 1 and transport.calls[0].destination_e164 == "+12025550125"
    assert transport.calls[0].body == "New website enquiry.\nName: Oleh\nMessage: Call me back"
    record = next(iter(audit.records.values()))
    assert record["delivery_role"] == SMSDeliveryRole.OWNER.value


def test_missing_owner_configuration_fails_closed_before_provider() -> None:
    state, audit, transport = State(), Audit(), Transport([SmsTransportOutcome.ACCEPTED])
    service = SmsDeliveryService(
        config=config(SMSDeliveryMode.OWNER_ALERT, owner_to_e164=None),
        state=state, audit=audit, transport=transport,
    )
    with pytest.raises(SmsDeliveryError, match="sms_owner_recipient_not_configured"):
        asyncio.run(service.send(
            example_id="TRUSTED_EXAMPLE", phone=None, message="Call me", first_name="Oleh",
            idempotency_key="sms-config-mode-key-0001", client_id="safe-client",
        ))
    assert transport.calls == [] and audit.records == {}


def test_both_has_two_independent_legs_and_partial_aggregate() -> None:
    result, _, audit, transport = exercise(
        SMSDeliveryMode.BOTH, [SmsTransportOutcome.ACCEPTED, SmsTransportOutcome.REJECTED]
    )
    assert result.outcome is SmsAggregateOutcome.VISITOR_ACCEPTED_OWNER_FAILED
    assert len(transport.calls) == 2
    assert [message.body for message in transport.calls] == [
        'Hi Oleh.\n\nYour message:\n\n"Call me back"\n\nThis is an example of how your customers can start an SMS conversation from your future website.\n\nSiteFormo',
        "New website enquiry.\nName: Oleh\nMessage: Call me back",
    ]
    assert {record["delivery_role"] for record in audit.records.values()} == {"VISITOR", "OWNER"}
    assert [record["provider_call_count"] for record in audit.records.values()] == ["1", "1"]


def test_both_timeout_like_outcome_has_no_retry() -> None:
    result, _, _, transport = exercise(
        SMSDeliveryMode.BOTH, [SmsTransportOutcome.QUARANTINED, SmsTransportOutcome.ACCEPTED]
    )
    assert result.outcome is SmsAggregateOutcome.OWNER_ACCEPTED_VISITOR_FAILED
    assert len(transport.calls) == 2


def test_both_replay_keeps_maximum_one_call_per_leg() -> None:
    state, audit = State(), Audit()
    transport = Transport([SmsTransportOutcome.ACCEPTED, SmsTransportOutcome.ACCEPTED])
    service = SmsDeliveryService(
        config=config(SMSDeliveryMode.BOTH), state=state, audit=audit, transport=transport
    )

    async def send_once():
        return await service.send(
            example_id="TRUSTED_EXAMPLE", phone="+12025550124", message="Call me back",
            first_name="Oleh", idempotency_key="sms-config-mode-key-0001", client_id="safe-client",
        )

    first = asyncio.run(send_once())
    replay = asyncio.run(send_once())
    assert first.outcome is SmsAggregateOutcome.BOTH_ACCEPTED
    assert replay.outcome is SmsAggregateOutcome.BOTH_ACCEPTED and replay.replayed is True
    assert len(transport.calls) == 2


def test_audit_is_hash_only_for_numbers_and_user_message() -> None:
    _, _, audit, _ = exercise(SMSDeliveryMode.BOTH, [SmsTransportOutcome.ACCEPTED, SmsTransportOutcome.ACCEPTED])
    serialized = json.dumps(audit.records)
    for raw in ("+12025550124", "+12025550125", "Call me back"):
        assert raw not in serialized
    assert all({
        "delivery_mode", "delivery_role", "message_length", "message_hash",
        "message_encoding", "message_segment_count", "recipient_hash",
    } <= set(record) for record in audit.records.values())


def test_both_dry_run_has_two_hash_only_audit_candidates_and_zero_calls() -> None:
    result = build_dry_run(
        first_name="Oleh", phone="+12025550124", owner_to="+12025550125",
        message="Call me back", idempotency_key="sms-config-mode-key-0001",
        countries=frozenset({"US"}), mode=SMSDeliveryMode.BOTH,
    )
    assert [leg["role"] for leg in result["legs"]] == ["VISITOR", "OWNER"]
    assert len(result["audit_candidates"]) == 2
    assert all(item["provider_call_count"] == 0 and item["transport_invoked"] is False for item in result["audit_candidates"])
    assert all(item["message_segment_count"] == 1 for item in result["audit_candidates"])
    serialized = json.dumps(result["audit_candidates"])
    assert "+12025550124" not in serialized and "+12025550125" not in serialized
    assert "Call me back" not in serialized
