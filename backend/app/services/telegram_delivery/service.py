from __future__ import annotations

import hashlib
from dataclasses import dataclass

from app.services.telegram_delivery.binding import BindingStore
from app.services.telegram_delivery.audit import AuditOutcome, DeliveryAuditStore
from app.services.telegram_delivery.models import BindingState, TelegramMessage, render_demo_message
from app.services.telegram_delivery.security import (
    parse_start_update,
    private_id_hash,
    token_hash,
    verify_webhook_secret,
)
from app.services.telegram_delivery.transport import TelegramTransport, TransportState


@dataclass(frozen=True, slots=True)
class WebhookResult:
    state: BindingState
    binding_id: str | None = None


class VisitorBindingWebhookService:
    def __init__(
        self, *, store: BindingStore, audit: DeliveryAuditStore,
        transport: TelegramTransport, webhook_secret: str,
    ) -> None:
        if not webhook_secret:
            raise ValueError("telegram_webhook_secret_required")
        self.store, self.audit = store, audit
        self.transport, self.webhook_secret = transport, webhook_secret

    async def handle(self, payload: object, supplied_secret: str | None, now_seconds: int) -> WebhookResult:
        if not verify_webhook_secret(self.webhook_secret, supplied_secret):
            return WebhookResult(BindingState.INVALID_UPDATE)
        try:
            update = parse_start_update(payload)
        except ValueError:
            return WebhookResult(BindingState.INVALID_UPDATE)
        digest = token_hash(update.token)
        try:
            consumed = await self.store.consume(
                token_digest=digest, update_digest=private_id_hash(update.update_id),
                chat_digest=private_id_hash(update.chat_id), now_seconds=now_seconds,
            )
        except Exception as exc:
            raise RuntimeError("telegram_binding_persistence_unavailable") from exc
        if consumed.state is not BindingState.CONSUMED:
            return WebhookResult(consumed.state, consumed.binding_id)
        binding_id = consumed.binding_id or "unknown"
        chat_digest = private_id_hash(update.chat_id)
        try:
            await self.audit.create(
                binding_id=binding_id, token_hash=digest,
                update_id_hash=private_id_hash(update.update_id), target_chat_id_hash=chat_digest,
                message_length=len(consumed.validated_message or ""),
                message_hash=hashlib.sha256((consumed.validated_message or "").encode("utf-8")).hexdigest(),
                now_seconds=now_seconds,
            )
            await self.audit.mark_transport_invoked(binding_id=binding_id, now_seconds=now_seconds)
        except Exception as exc:
            raise RuntimeError("telegram_delivery_audit_unavailable") from exc
        message = TelegramMessage(
            update.chat_id,
            render_demo_message(consumed.validated_name, consumed.validated_message or ""),
            binding_id,
        )
        result = await self.transport.send(message)
        message_id_hash = (
            hashlib.sha256(result.provider_message_id.encode()).hexdigest()
            if result.provider_message_id and result.message_id_present else ""
        )
        if (
            result.state is TransportState.ACCEPTED and result.provider_ok is True
            and result.message_id_present and result.provider_message_id
            and result.http_status is not None and 200 <= result.http_status < 300
        ):
            await self.store.finalize(
                token_digest=digest, state=BindingState.DELIVERED,
                provider_reference_hash=message_id_hash,
            )
            final_state, outcome = BindingState.DELIVERED, AuditOutcome.ACCEPTED
        elif result.state in {TransportState.AMBIGUOUS, TransportState.TIMEOUT, TransportState.TRANSIENT_FAILURE}:
            await self.store.finalize(
                token_digest=digest, state=BindingState.QUARANTINED, provider_reference_hash="",
            )
            final_state = BindingState.QUARANTINED
            outcome = AuditOutcome.QUARANTINED if result.state in {TransportState.TIMEOUT, TransportState.TRANSIENT_FAILURE} else AuditOutcome.AMBIGUOUS
        else:
            final_state, outcome = BindingState.CONSUMED, AuditOutcome.REJECTED
        try:
            await self.audit.finalize(
                binding_id=binding_id, outcome=outcome, final_state=final_state,
                http_status=result.http_status, provider_ok=result.provider_ok,
                message_id_hash=message_id_hash, now_seconds=now_seconds,
            )
        except Exception as exc:
            raise RuntimeError("telegram_delivery_audit_finalize_failed") from exc
        return WebhookResult(final_state, consumed.binding_id)
