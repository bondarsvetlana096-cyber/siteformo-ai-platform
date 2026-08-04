from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Protocol

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import redis.asyncio as redis

from app.services.sms_delivery.audit import SMS_AUDIT_NAMESPACE

DELIVERY_ID = re.compile(r"^[0-9a-f]{24}$", re.ASCII)
SHA256 = re.compile(r"^[0-9a-f]{64}$", re.ASCII)
READ_ERROR_CODES = {
    "audit_identity_invalid",
    "audit_namespace_invalid",
    "audit_record_missing",
    "audit_schema_mismatch",
    "audit_store_unavailable",
}
OUTPUT_FIELDS = (
    "delivery_id",
    "idempotency_hash",
    "recipient_hash",
    "delivery_mode",
    "delivery_role",
    "message_length",
    "message_hash",
    "message_encoding",
    "message_segment_count",
    "transport_invoked",
    "http_status",
    "message_sid_present",
    "message_sid_hash",
    "typed_outcome",
    "provider_call_count",
    "final_state",
    "created_at",
    "updated_at",
    "expires_at",
)


class AuditClient(Protocol):
    async def hgetall(self, key: str) -> Mapping[str, str]: ...
    async def aclose(self) -> None: ...


class AuditReadError(RuntimeError):
    pass


def validate_delivery_id(value: str) -> str:
    if not DELIVERY_ID.fullmatch(value):
        raise AuditReadError("audit_identity_invalid")
    return value


def build_audit_key(delivery_id: str, namespace: str = SMS_AUDIT_NAMESPACE) -> str:
    if namespace != SMS_AUDIT_NAMESPACE:
        raise AuditReadError("audit_namespace_invalid")
    return f"{namespace}:delivery:{validate_delivery_id(delivery_id)}"


def _integer(value: str, *, minimum: int = 0, maximum: int | None = None) -> bool:
    if not value.isdigit():
        return False
    number = int(value)
    return number >= minimum and (maximum is None or number <= maximum)


def allowlisted_record(delivery_id: str, record: Mapping[str, str]) -> dict[str, str]:
    if not record:
        raise AuditReadError("audit_record_missing")
    if any(field not in record for field in OUTPUT_FIELDS):
        raise AuditReadError("audit_schema_mismatch")
    output = {field: str(record[field]) for field in OUTPUT_FIELDS}
    valid = (
        output["delivery_id"] == delivery_id
        and SHA256.fullmatch(output["idempotency_hash"]) is not None
        and SHA256.fullmatch(output["recipient_hash"]) is not None
        and output["delivery_mode"] in {"VISITOR_NOTIFICATION", "OWNER_ALERT", "BOTH"}
        and output["delivery_role"] in {"VISITOR", "OWNER"}
        and _integer(output["message_length"], minimum=1)
        and SHA256.fullmatch(output["message_hash"]) is not None
        and output["message_encoding"] in {"GSM-7", "UCS-2"}
        and _integer(output["message_segment_count"], minimum=1)
        and output["transport_invoked"] in {"true", "false"}
        and _integer(output["http_status"], minimum=100, maximum=599)
        and output["message_sid_present"] in {"true", "false"}
        and (SHA256.fullmatch(output["message_sid_hash"]) is not None if output["message_sid_present"] == "true" else output["message_sid_hash"] == "")
        and output["typed_outcome"] in {"ACCEPTED", "REJECTED", "AMBIGUOUS", "QUARANTINED"}
        and output["provider_call_count"] in {"0", "1"}
        and output["final_state"] in {"DELIVERED", "REJECTED", "QUARANTINED"}
        and _integer(output["created_at"], minimum=1)
        and _integer(output["updated_at"], minimum=1)
        and _integer(output["expires_at"], minimum=1)
    )
    if not valid:
        raise AuditReadError("audit_schema_mismatch")
    return output


async def read_audit_once(
    delivery_id: str,
    redis_url: str,
    client_factory: Callable[..., AuditClient] = redis.Redis.from_url,
) -> dict[str, str]:
    key = build_audit_key(delivery_id)
    if not redis_url:
        raise AuditReadError("audit_store_unavailable")
    try:
        client = client_factory(redis_url, decode_responses=True)
    except Exception as exc:
        raise AuditReadError("audit_store_unavailable") from exc
    try:
        record = await client.hgetall(key)
    except Exception as exc:
        raise AuditReadError("audit_store_unavailable") from exc
    finally:
        try:
            await client.aclose()
        except Exception:
            pass
    return allowlisted_record(delivery_id, record)


def serialize_record(record: Mapping[str, str]) -> str:
    return json.dumps({field: record[field] for field in OUTPUT_FIELDS}, separators=(",", ":"))


def main() -> int:
    parser = argparse.ArgumentParser(description="Read one fixed-schema hash-only SMS audit record.")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--delivery-id")
    group.add_argument("--validate-delivery-id")
    args = parser.parse_args()
    try:
        if args.validate_delivery_id is not None:
            value = validate_delivery_id(args.validate_delivery_id)
            print(json.dumps({"delivery_id": value, "status": "valid"}, separators=(",", ":")))
            return 0
        result = asyncio.run(read_audit_once(args.delivery_id, os.environ.get("REDIS_URL", "")))
        print(serialize_record(result))
        return 0
    except AuditReadError as exc:
        code = str(exc) if str(exc) in READ_ERROR_CODES else "audit_read_failed"
        print(json.dumps({"error": code}, separators=(",", ":")), file=sys.stderr)
        return 2
    except Exception:
        print(json.dumps({"error": "audit_read_failed"}, separators=(",", ":")), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
