from __future__ import annotations

import argparse
import hashlib
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Mapping, Protocol

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.sms_delivery.configuration import SmsConfiguration, resolve_sms_configuration
from app.services.sms_delivery.models import (
    analyze_sms_segments,
    render_visitor_notification,
    validate_destination,
    validate_idempotency_key,
)


class HttpResponse(Protocol):
    status_code: int
    def json(self) -> object: ...


class HttpClient(Protocol):
    def post(self, url: str, **kwargs: object) -> HttpResponse: ...
    def close(self) -> None: ...


@dataclass(frozen=True, slots=True)
class SmokeRequest:
    endpoint: str
    origin: str
    recipient: str
    first_name: str | None
    message: str
    idempotency_key: str
    execute: bool
    owner_authorized: bool


def mask_phone(phone: str) -> str:
    return phone[:3] + "*" * max(3, len(phone) - 6) + phone[-3:]


def safe_plan(request: SmokeRequest, config: SmsConfiguration) -> dict[str, object]:
    recipient = validate_destination(request.recipient, config.allowed_countries)
    validate_idempotency_key(request.idempotency_key)
    body = render_visitor_notification(request.first_name, request.message)
    segment_info = analyze_sms_segments(body)
    return {
        "endpoint": request.endpoint,
        "origin": request.origin,
        "recipient": mask_phone(recipient),
        "sender": mask_phone(config.sender_e164 or ""),
        "message_length": len(body),
        "message_hash": hashlib.sha256(body.encode()).hexdigest(),
        "message_encoding": segment_info.encoding,
        "message_segment_count": segment_info.segment_count,
        "execute": request.execute,
        "owner_authorized": request.owner_authorized,
        "typed_outcome": "DRY_RUN" if not request.execute else "PENDING_SINGLE_CALL",
        "provider_call_count": 0,
    }


def run_smoke(
    request: SmokeRequest,
    environment: Mapping[str, str],
    client_factory: Callable[..., HttpClient] | None = None,
) -> tuple[int, dict[str, object]]:
    config = resolve_sms_configuration(environment)
    plan = safe_plan(request, config)
    if not request.execute:
        return 0, plan
    if not request.owner_authorized:
        return 2, {**plan, "typed_outcome": "BLOCKED_OWNER_AUTHORIZATION_REQUIRED"}
    try:
        config.require_ready()
    except ValueError:
        return 2, {**plan, "typed_outcome": "BLOCKED_CONFIGURATION_NOT_READY"}
    if not request.endpoint.startswith("https://") or not request.origin.startswith("https://"):
        return 2, {**plan, "typed_outcome": "BLOCKED_INVALID_ENDPOINT_OR_ORIGIN"}
    if client_factory is None:
        import httpx
        client_factory = httpx.Client
    client = client_factory(timeout=20.0)
    try:
        response = client.post(
            request.endpoint,
            headers={"Origin": request.origin, "Content-Type": "application/json"},
            json={
                "first_name": request.first_name,
                "phone": request.recipient,
                "message": request.message,
                "idempotency_key": request.idempotency_key,
            },
        )
    finally:
        client.close()
    try:
        body = response.json()
    except ValueError:
        body = None
    accepted = (
        response.status_code == 201
        and isinstance(body, dict)
        and body.get("status") == "accepted"
        and isinstance(body.get("delivery_reference"), str)
    )
    return (0 if accepted else 3), {
        **plan,
        "http_status": response.status_code,
        "typed_outcome": "ACCEPTED" if accepted else "AMBIGUOUS",
        "provider_call_count": 1,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="One-call SMS production smoke candidate; dry-run by default.")
    parser.add_argument("--endpoint", default="https://siteformo-ai-platform-production.up.railway.app/api/demo/sms/start")
    parser.add_argument("--origin", default="https://dev.siteformo.com")
    parser.add_argument(
        "--recipient",
        help="Recipient E.164; omit to read SITEFORMO_SMS_SMOKE_RECIPIENT from the process environment.",
    )
    parser.add_argument("--first-name")
    parser.add_argument("--message", required=True)
    parser.add_argument("--idempotency-key", required=True)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--owner-authorized", action="store_true")
    args = parser.parse_args()
    import os
    recipient = args.recipient or os.environ.get("SITEFORMO_SMS_SMOKE_RECIPIENT", "")
    code, result = run_smoke(
        SmokeRequest(args.endpoint, args.origin, recipient, args.first_name, args.message, args.idempotency_key, args.execute, args.owner_authorized),
        os.environ,
    )
    print(json.dumps(result, indent=2))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
