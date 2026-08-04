from __future__ import annotations

import asyncio
import json

import pytest

from app.services.sms_delivery.audit import SMS_AUDIT_NAMESPACE
from scripts.read_sms_audit_once import (
    OUTPUT_FIELDS,
    AuditReadError,
    allowlisted_record,
    build_audit_key,
    read_audit_once,
    serialize_record,
    validate_delivery_id,
)

DELIVERY_ID = "a" * 24
HASH = "b" * 64
SID_HASH = "c" * 64


def valid_record(**changes: str) -> dict[str, str]:
    record = {
        "delivery_id": DELIVERY_ID,
        "idempotency_hash": HASH,
        "recipient_hash": HASH,
        "delivery_mode": "VISITOR_NOTIFICATION",
        "delivery_role": "VISITOR",
        "message_length": "49",
        "message_hash": HASH,
        "message_encoding": "GSM-7",
        "message_segment_count": "1",
        "transport_invoked": "true",
        "http_status": "201",
        "message_sid_present": "true",
        "message_sid_hash": SID_HASH,
        "typed_outcome": "ACCEPTED",
        "provider_call_count": "1",
        "final_state": "DELIVERED",
        "created_at": "1785819600",
        "updated_at": "1785819601",
        "expires_at": "1786424400",
    }
    record.update(changes)
    return record


class ReadOnlyClient:
    def __init__(self, record: dict[str, str]) -> None:
        self.record = record
        self.calls: list[tuple[str, str]] = []
        self.closed = False

    async def hgetall(self, key: str) -> dict[str, str]:
        self.calls.append(("hgetall", key))
        return dict(self.record)

    async def aclose(self) -> None:
        self.closed = True

    def __getattr__(self, name: str):
        raise AssertionError(f"forbidden_redis_operation:{name}")


def test_valid_record_is_one_exact_read_with_deterministic_allowlisted_output() -> None:
    client = ReadOnlyClient(valid_record())
    result = asyncio.run(read_audit_once(DELIVERY_ID, "redis://offline", lambda *args, **kwargs: client))
    assert tuple(result) == OUTPUT_FIELDS
    assert client.calls == [("hgetall", f"{SMS_AUDIT_NAMESPACE}:delivery:{DELIVERY_ID}")]
    assert client.closed is True
    reversed_input = dict(reversed(list(valid_record().items())))
    assert serialize_record(result) == serialize_record(allowlisted_record(DELIVERY_ID, reversed_input))
    assert list(json.loads(serialize_record(result))) == list(OUTPUT_FIELDS)


@pytest.mark.parametrize(
    ("field", "raw_value"),
    [
        ("raw_phone", "+353" + "860000000"),
        ("raw_message", "private visitor message"),
        ("raw_message_sid", "SM" + "1" * 32),
        ("unknown_field", "must be ignored"),
    ],
)
def test_forbidden_and_unknown_fields_are_ignored(field: str, raw_value: str) -> None:
    output = serialize_record(allowlisted_record(DELIVERY_ID, valid_record(**{field: raw_value})))
    assert field not in output
    assert raw_value not in output


@pytest.mark.parametrize("identity", ["", "A" * 24, "a" * 23, "a" * 25, "../secret", "sf:demo-sms:v1:audit:delivery:" + DELIVERY_ID, "*"])
def test_malformed_identity_and_arbitrary_key_attempts_are_rejected(identity: str) -> None:
    with pytest.raises(AuditReadError, match="audit_identity_invalid"):
        validate_delivery_id(identity)


def test_wrong_namespace_is_rejected() -> None:
    with pytest.raises(AuditReadError, match="audit_namespace_invalid"):
        build_audit_key(DELIVERY_ID, "sf:other")


def test_missing_record_is_rejected() -> None:
    client = ReadOnlyClient({})
    with pytest.raises(AuditReadError, match="audit_record_missing"):
        asyncio.run(read_audit_once(DELIVERY_ID, "redis://offline", lambda *args, **kwargs: client))
    assert client.calls == [("hgetall", f"{SMS_AUDIT_NAMESPACE}:delivery:{DELIVERY_ID}")]


@pytest.mark.parametrize(
    "changes",
    [
        {"delivery_id": "d" * 24},
        {"recipient_hash": "raw"},
        {"message_segment_count": "0"},
        {"http_status": ""},
        {"message_sid_present": "true", "message_sid_hash": ""},
        {"provider_call_count": "2"},
        {"final_state": "PENDING"},
    ],
)
def test_schema_mismatch_is_rejected(changes: dict[str, str]) -> None:
    with pytest.raises(AuditReadError, match="audit_schema_mismatch"):
        allowlisted_record(DELIVERY_ID, valid_record(**changes))


def test_reader_has_no_write_scan_or_wildcard_path() -> None:
    client = ReadOnlyClient(valid_record())
    asyncio.run(read_audit_once(DELIVERY_ID, "redis://offline", lambda *args, **kwargs: client))
    assert [name for name, _ in client.calls] == ["hgetall"]
    assert "*" not in client.calls[0][1]
