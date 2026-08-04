from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass
from enum import StrEnum

from app.services.delivery.contracts import ClaimKind, DeliveryIdentity, DeliveryState
from app.services.sms_delivery.audit import SmsAuditStore
from app.services.sms_delivery.configuration import SmsConfiguration
from app.services.sms_delivery.models import (
    SMSDeliveryMode,
    SMSDeliveryRole,
    SmsMessage,
    normalize_first_name,
    normalize_message,
    render_owner_alert,
    render_visitor_notification,
    validate_destination,
    validate_idempotency_key,
)
from app.services.sms_delivery.transport import SmsTransport, SmsTransportOutcome


class SmsDeliveryError(RuntimeError):
    def __init__(self, code: str, status_code: int) -> None:
        super().__init__(code)
        self.code = code
        self.status_code = status_code


class SmsAggregateOutcome(StrEnum):
    ACCEPTED = "ACCEPTED"
    BOTH_ACCEPTED = "BOTH_ACCEPTED"
    VISITOR_ACCEPTED_OWNER_FAILED = "VISITOR_ACCEPTED_OWNER_FAILED"
    OWNER_ACCEPTED_VISITOR_FAILED = "OWNER_ACCEPTED_VISITOR_FAILED"
    REJECTED = "REJECTED"
    AMBIGUOUS = "AMBIGUOUS"
    QUARANTINED = "QUARANTINED"


@dataclass(frozen=True, slots=True)
class SmsLegResult:
    role: SMSDeliveryRole
    outcome: SmsTransportOutcome
    replayed: bool
    delivery_reference: str


@dataclass(frozen=True, slots=True)
class SmsDeliveryResult:
    outcome: SmsAggregateOutcome | SmsTransportOutcome
    delivery_reference: str
    replayed: bool
    legs: tuple[SmsLegResult, ...]


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


class SmsDeliveryService:
    def __init__(self, *, config: SmsConfiguration, state: DeliveryState, audit: SmsAuditStore, transport: SmsTransport) -> None:
        self.config, self.state, self.audit, self.transport = config, state, audit, transport

    @staticmethod
    def identity(
        *, example_id: str, recipient: str, parent_idempotency_key: str,
        role: SMSDeliveryRole, body: str, client_id: str,
    ) -> DeliveryIdentity:
        child_key = f"{parent_idempotency_key}:{role.value.lower()}"
        canonical = {
            "channel": "SMS", "trusted_example_id": example_id, "recipient": recipient,
            "role": role.value, "message_hash": _hash(body), "contract": "SITEFORMO_SMS_DELIVERY_V1",
        }
        return DeliveryIdentity(
            channel=f"SMS:{role.value}", example_id=_hash(example_id)[:32], recipient_hash=_hash(recipient),
            idempotency_hash=_hash(child_key),
            fingerprint=_hash(json.dumps(canonical, sort_keys=True, separators=(",", ":"))),
            client_hash=_hash(client_id),
        )

    async def _send_leg(
        self, *, example_id: str, recipient: str, body: str, role: SMSDeliveryRole,
        mode: SMSDeliveryMode, parent_idempotency_key: str, client_id: str,
    ) -> SmsLegResult:
        identity = self.identity(
            example_id=example_id, recipient=recipient, parent_idempotency_key=parent_idempotency_key,
            role=role, body=body, client_id=client_id,
        )
        claim = await self.state.claim(identity)
        reference = "sms_" + identity.idempotency_hash[:24]
        if claim.kind is ClaimKind.REPLAY_ACCEPTED:
            return SmsLegResult(role, SmsTransportOutcome.ACCEPTED, True, reference)
        if claim.kind is not ClaimKind.ACQUIRED:
            mapping = {
                ClaimKind.QUOTA_EXHAUSTED: ("sms_quota_exhausted", 429),
                ClaimKind.RATE_LIMITED: ("sms_rate_limited", 429),
                ClaimKind.CONFLICT: ("sms_idempotency_conflict", 409),
                ClaimKind.IN_PROGRESS: ("sms_in_progress", 409),
                ClaimKind.REPLAY_QUARANTINED: ("sms_outcome_quarantined", 502),
            }
            code, status = mapping[claim.kind]
            raise SmsDeliveryError(code, status)
        now = int(time.time())
        delivery_id = identity.idempotency_hash[:24]
        initial = {
            "idempotency_hash": identity.idempotency_hash,
            "recipient_hash": identity.recipient_hash,
            "origin_hash": identity.client_hash,
            "example_hash": identity.example_id,
            "delivery_mode": mode.value,
            "delivery_role": role.value,
            "message_length": str(len(body)),
            "message_hash": _hash(body),
            "transport_invoked": "false", "provider_call_count": "0", "http_status": "",
            "message_sid_present": "false", "message_sid_hash": "", "typed_outcome": "",
            "final_state": "PENDING", "created_at": str(now), "updated_at": str(now),
            "expires_at": str(now + self.config.audit_ttl_seconds),
        }
        try:
            await self.audit.create(delivery_id, initial, self.config.audit_ttl_seconds)
        except Exception as exc:
            await self.state.release(identity, "sms_audit_unavailable")
            raise SmsDeliveryError("sms_audit_unavailable", 503) from exc
        result = await self.transport.send(SmsMessage(recipient, body), identity.idempotency_hash)
        sid_hash = _hash(result.provider_message_sid) if result.provider_message_sid else ""
        final_state = "DELIVERED" if result.outcome is SmsTransportOutcome.ACCEPTED else (
            "QUARANTINED" if result.outcome in {SmsTransportOutcome.AMBIGUOUS, SmsTransportOutcome.QUARANTINED} else "REJECTED"
        )
        try:
            await self.audit.finalize(delivery_id, {
                "transport_invoked": "true", "provider_call_count": "1",
                "http_status": "" if result.http_status is None else str(result.http_status),
                "message_sid_present": str(bool(sid_hash)).lower(), "message_sid_hash": sid_hash,
                "typed_outcome": result.outcome.value, "final_state": final_state,
                "updated_at": str(int(time.time())),
            })
        except Exception as exc:
            await self.state.quarantine(identity, "sms_audit_finalize_failed")
            raise SmsDeliveryError("sms_audit_unavailable", 503) from exc
        if result.outcome is SmsTransportOutcome.ACCEPTED and result.provider_message_sid:
            await self.state.accept(identity, result.provider_message_sid)
            return SmsLegResult(role, result.outcome, False, reference)
        if result.outcome in {SmsTransportOutcome.AMBIGUOUS, SmsTransportOutcome.QUARANTINED}:
            await self.state.quarantine(identity, "sms_provider_outcome_ambiguous")
        else:
            await self.state.release(identity, "sms_provider_rejected")
        return SmsLegResult(role, result.outcome, False, reference)

    async def send(
        self, *, example_id: str, phone: str | None, message: str | None,
        first_name: str | None, idempotency_key: str, client_id: str,
    ) -> SmsDeliveryResult:
        try:
            self.config.require_ready()
            key = validate_idempotency_key(idempotency_key)
            name = normalize_first_name(first_name)
            enquiry = normalize_message(message, required=True)
            visitor = validate_destination(phone or "", self.config.allowed_countries) if self.config.delivery_mode in {
                SMSDeliveryMode.VISITOR_NOTIFICATION, SMSDeliveryMode.BOTH,
            } or self.config.owner_requires_visitor_contact else None
        except ValueError as exc:
            code = str(exc)
            status = 503 if any(part in code for part in ("configured", "disabled")) else 422
            raise SmsDeliveryError(code, status) from exc

        specs: list[tuple[SMSDeliveryRole, str, str]] = []
        if self.config.delivery_mode in {SMSDeliveryMode.VISITOR_NOTIFICATION, SMSDeliveryMode.BOTH}:
            specs.append((SMSDeliveryRole.VISITOR, visitor or "", render_visitor_notification(name, enquiry)))
        if self.config.delivery_mode in {SMSDeliveryMode.OWNER_ALERT, SMSDeliveryMode.BOTH}:
            specs.append((
                SMSDeliveryRole.OWNER, self.config.owner_to_e164 or "",
                render_owner_alert(name, visitor if self.config.owner_requires_visitor_contact else None, enquiry),
            ))

        legs: list[SmsLegResult] = []
        failures: list[SmsDeliveryError] = []
        for role, recipient, body in specs:
            try:
                legs.append(await self._send_leg(
                    example_id=example_id, recipient=recipient, body=body, role=role,
                    mode=self.config.delivery_mode, parent_idempotency_key=key, client_id=client_id,
                ))
            except SmsDeliveryError as exc:
                failures.append(exc)

        accepted = {leg.role for leg in legs if leg.outcome is SmsTransportOutcome.ACCEPTED}
        if failures and not legs:
            raise failures[0]
        if self.config.delivery_mode is SMSDeliveryMode.BOTH:
            if accepted == {SMSDeliveryRole.VISITOR, SMSDeliveryRole.OWNER}:
                aggregate = SmsAggregateOutcome.BOTH_ACCEPTED
            elif accepted == {SMSDeliveryRole.VISITOR}:
                aggregate = SmsAggregateOutcome.VISITOR_ACCEPTED_OWNER_FAILED
            elif accepted == {SMSDeliveryRole.OWNER}:
                aggregate = SmsAggregateOutcome.OWNER_ACCEPTED_VISITOR_FAILED
            elif any(leg.outcome in {SmsTransportOutcome.AMBIGUOUS, SmsTransportOutcome.QUARANTINED} for leg in legs):
                aggregate = SmsAggregateOutcome.QUARANTINED
            else:
                aggregate = SmsAggregateOutcome.REJECTED
        elif accepted:
            aggregate = SmsTransportOutcome.ACCEPTED
        elif any(leg.outcome in {SmsTransportOutcome.AMBIGUOUS, SmsTransportOutcome.QUARANTINED} for leg in legs):
            raise SmsDeliveryError("sms_provider_quarantined", 502)
        else:
            raise SmsDeliveryError("sms_provider_rejected", 502)
        reference = "sms_" + _hash(key)[:24]
        return SmsDeliveryResult(aggregate, reference, bool(legs) and all(leg.replayed for leg in legs), tuple(legs))
