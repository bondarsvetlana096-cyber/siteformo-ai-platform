from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from collections.abc import Callable

from app.services.delivery.contracts import ClaimKind, DeliveryIdentity, DeliveryState
from app.services.whatsapp_delivery.models import (
    MESSAGE_CONTRACT_ID,
    MESSAGE_CONTRACT_VERSION,
    WhatsAppMessage,
    normalize_e164,
    render_demo_message,
)
from app.services.whatsapp_delivery.transport import TransportState, WhatsAppTransport


class DeliveryError(RuntimeError):
    def __init__(self, code: str, status_code: int) -> None:
        super().__init__(code)
        self.code = code
        self.status_code = status_code


class WhatsAppDeliveryService:
    def __init__(
        self,
        state: DeliveryState,
        transport: WhatsAppTransport,
        readiness_check: Callable[[], None] | None = None,
    ) -> None:
        self.state = state
        self.transport = transport
        self.readiness_check = readiness_check

    @staticmethod
    def identity(
        *, example_id: str, phone: str, idempotency_key: str, payload: dict[str, str], client_id: str
    ) -> DeliveryIdentity:
        canonical = dict(payload)
        canonical.update(
            {
                "channel": "WHATSAPP",
                "trusted_example_id": example_id,
                "contact_value": phone,
                "template_id": MESSAGE_CONTRACT_ID,
                "template_version": MESSAGE_CONTRACT_VERSION,
                "locale": "en",
                "transport": "twilio_whatsapp",
            }
        )
        fingerprint = hashlib.sha256(
            json.dumps(canonical, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
        ).hexdigest()
        return DeliveryIdentity(
            channel="WHATSAPP",
            example_id=hashlib.sha256(example_id.encode()).hexdigest()[:32],
            recipient_hash=hashlib.sha256(phone.encode()).hexdigest(),
            idempotency_hash=hashlib.sha256(idempotency_key.encode()).hexdigest(),
            fingerprint=fingerprint,
            client_hash=hashlib.sha256(client_id.encode()).hexdigest(),
        )

    async def send(
        self,
        *,
        example_id: str,
        phone: str,
        name: str,
        message: str,
        idempotency_key: str,
        client_id: str,
    ) -> tuple[str, str, bool, int]:
        # Fail before normalization, Redis quota/rate mutation, or transport construction/use.
        if self.readiness_check is None:
            raise DeliveryError("whatsapp_public_delivery_not_ready", 503)
        self.readiness_check()
        normalized_phone = normalize_e164(phone)
        payload = {
            "name": name,
            "phone": normalized_phone,
            "message": message,
        }
        identity = self.identity(
            example_id=example_id,
            phone=normalized_phone,
            idempotency_key=idempotency_key,
            payload=payload,
            client_id=client_id,
        )
        claim = await self.state.claim(identity)
        errors = {
            ClaimKind.QUOTA_EXHAUSTED: ("quota_exhausted", 429),
            ClaimKind.RATE_LIMITED: ("rate_limited", 429),
            ClaimKind.CONFLICT: ("idempotency_conflict", 409),
            ClaimKind.IN_PROGRESS: ("submission_in_progress", 409),
            ClaimKind.REPLAY_QUARANTINED: (claim.failure_code or "provider_outcome_ambiguous", 502),
        }
        if claim.kind in errors:
            code, status = errors[claim.kind]
            raise DeliveryError(code, status)
        if claim.kind is ClaimKind.REPLAY_ACCEPTED and claim.provider_message_id:
            return "provider_accepted", self.public_reference(claim.provider_message_id), True, claim.remaining_deliveries or 0

        rendered = render_demo_message(name, identity.idempotency_hash[:16])
        provider_message: WhatsAppMessage = replace(rendered, destination_e164=normalized_phone)
        result = await self.transport.send(provider_message, identity.idempotency_hash)
        if result.state is TransportState.ACCEPTED and result.provider_message_id:
            remaining = await self.state.accept(identity, result.provider_message_id)
            return "provider_accepted", self.public_reference(result.provider_message_id), False, remaining
        if result.state is TransportState.AMBIGUOUS_ACCEPTANCE or (
            result.state is TransportState.TIMEOUT and result.transport_invoked
        ):
            await self.state.quarantine(identity, "provider_outcome_ambiguous")
            raise DeliveryError("provider_outcome_ambiguous", 504)
        if result.state is TransportState.TIMEOUT:
            await self.state.release(identity, "provider_timeout")
            raise DeliveryError("provider_timeout", 504)

        mapping = {
            TransportState.REJECTED: ("provider_rejected", 502),
            TransportState.AUTHENTICATION_ERROR: ("provider_authentication_failed", 503),
            TransportState.CONFIGURATION_ERROR: ("provider_configuration_error", 503),
            TransportState.INVALID_DESTINATION: ("provider_invalid_destination", 422),
            TransportState.RATE_LIMITED: ("provider_rate_limited", 503),
            TransportState.TRANSIENT_FAILURE: ("provider_unavailable", 503),
        }
        code, status = mapping[result.state]
        await self.state.release(identity, code)
        raise DeliveryError(code, status)

    @staticmethod
    def public_reference(provider_message_id: str) -> str:
        return "wa_" + hashlib.sha256(provider_message_id.encode()).hexdigest()[:24]
