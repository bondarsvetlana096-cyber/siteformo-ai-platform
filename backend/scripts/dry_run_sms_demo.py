from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.sms_delivery.models import (
    SMSDeliveryMode, analyze_sms_segments, normalize_message, render_owner_alert,
    render_visitor_notification, validate_destination, validate_user_message,
)


def masked_phone(phone: str) -> str:
    return phone[:3] + "*" * max(3, len(phone) - 6) + phone[-3:]


def build_dry_run(*, first_name: str | None, phone: str | None, message: str, idempotency_key: str, countries: frozenset[str], mode: SMSDeliveryMode = SMSDeliveryMode.VISITOR_NOTIFICATION, owner_to: str | None = None) -> dict[str, object]:
    enquiry = validate_user_message(message)
    idempotency_hash = hashlib.sha256(idempotency_key.encode()).hexdigest()
    visitor = validate_destination(phone or "", countries) if mode in {SMSDeliveryMode.VISITOR_NOTIFICATION, SMSDeliveryMode.BOTH} else None
    owner = validate_destination(owner_to or "", countries) if mode in {SMSDeliveryMode.OWNER_ALERT, SMSDeliveryMode.BOTH} else None
    legs = []
    if visitor:
        body = render_visitor_notification(first_name, enquiry)
        legs.append(("VISITOR", visitor, body))
    if owner:
        body = render_owner_alert(first_name, None, enquiry)
        legs.append(("OWNER", owner, body))
    audits = [{
        "delivery_id": hashlib.sha256(f"{idempotency_key}:{role.lower()}".encode()).hexdigest()[:24],
        "idempotency_hash": hashlib.sha256(f"{idempotency_key}:{role.lower()}".encode()).hexdigest(),
        "recipient_hash": hashlib.sha256(recipient.encode()).hexdigest(),
        "delivery_mode": mode.value, "delivery_role": role,
        "message_length": len(body), "message_hash": hashlib.sha256(body.encode()).hexdigest(),
        "message_encoding": analyze_sms_segments(body).encoding,
        "message_segment_count": analyze_sms_segments(body).segment_count,
        "transport_invoked": False, "provider_call_count": 0, "http_status": None,
        "message_sid_present": False, "message_sid_hash": "", "typed_outcome": "DRY_RUN",
        "final_state": "NOT_DISPATCHED",
    } for role, recipient, body in legs]
    return {
        "request": {"first_name_present": bool(first_name), "visitor_phone": masked_phone(visitor) if visitor else None, "message_length": len(enquiry), "idempotency_key_hash": idempotency_hash},
        "normalized_phone": masked_phone(visitor) if visitor else None,
        "delivery_mode": mode.value,
        "legs": [{
            "role": role,
            "recipient": masked_phone(recipient),
            "message_length": len(body),
            "message_hash": hashlib.sha256(body.encode()).hexdigest(),
            "message_encoding": analyze_sms_segments(body).encoding,
            "message_segment_count": analyze_sms_segments(body).segment_count,
        } for role, recipient, body in legs],
        "typed_outcome": "DRY_RUN",
        "audit_candidate": audits[0],
        "audit_candidates": audits,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Offline SMS demo renderer; never creates an HTTP client.")
    parser.add_argument("--phone")
    parser.add_argument("--owner-to")
    parser.add_argument("--mode", choices=tuple(mode.value for mode in SMSDeliveryMode), default=SMSDeliveryMode.VISITOR_NOTIFICATION.value)
    parser.add_argument("--first-name")
    parser.add_argument("--message", required=True)
    parser.add_argument("--idempotency-key", required=True)
    parser.add_argument("--allowed-country", action="append", required=True, choices=("US", "GB", "IE"))
    args = parser.parse_args()
    print(json.dumps(build_dry_run(first_name=args.first_name, phone=args.phone, message=args.message, idempotency_key=args.idempotency_key, countries=frozenset(args.allowed_country), mode=SMSDeliveryMode(args.mode), owner_to=args.owner_to), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
