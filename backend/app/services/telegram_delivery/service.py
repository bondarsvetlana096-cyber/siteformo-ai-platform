from __future__ import annotations

import hashlib
from dataclasses import dataclass

from app.services.telegram_delivery.binding import BindingStore
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
    def __init__(self, *, store: BindingStore, transport: TelegramTransport, webhook_secret: str) -> None:
        if not webhook_secret:
            raise ValueError("telegram_webhook_secret_required")
        self.store, self.transport, self.webhook_secret = store, transport, webhook_secret

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
        message = TelegramMessage(
            update.chat_id, render_demo_message(consumed.validated_name), consumed.binding_id or "unknown"
        )
        result = await self.transport.send(message)
        if result.state is TransportState.ACCEPTED and result.provider_message_id:
            await self.store.finalize(
                token_digest=digest, state=BindingState.DELIVERED,
                provider_reference_hash=hashlib.sha256(result.provider_message_id.encode()).hexdigest(),
            )
            return WebhookResult(BindingState.DELIVERED, consumed.binding_id)
        if result.state in {TransportState.AMBIGUOUS, TransportState.TIMEOUT}:
            await self.store.finalize(
                token_digest=digest, state=BindingState.QUARANTINED, provider_reference_hash="",
            )
            return WebhookResult(BindingState.QUARANTINED, consumed.binding_id)
        return WebhookResult(BindingState.CONSUMED, consumed.binding_id)
