from __future__ import annotations

import hashlib
import time
import uuid

from app.services.voice_delivery.configuration import VoiceConfiguration
from app.services.voice_delivery.models import (
    ScheduleResult, VoiceRequest, VoiceState, digest, normalize_name,
    validate_idempotency, validate_phone,
)
from app.services.voice_delivery.store import VoiceStore
from app.services.voice_delivery.transport import TwilioVoiceTransport


class VoiceDemoService:
    def __init__(self, config: VoiceConfiguration, store: VoiceStore) -> None:
        self.config, self.store = config, store

    async def request_call(
        self, *, example_id: str, first_name: str, phone: str, idempotency_key: str,
        client_id: str, now_seconds: int | None = None,
    ) -> ScheduleResult:
        name = normalize_name(first_name)
        destination = validate_phone(phone, self.config.allowed_countries)
        key = validate_idempotency(idempotency_key)
        now = int(time.time()) if now_seconds is None else now_seconds
        request = VoiceRequest(
            request_id=uuid.uuid4().hex, example_hash=digest(example_id)[:32],
            first_name=name, phone_e164=destination,
            recipient_hash=digest(destination), idempotency_hash=digest(f"{example_id}:{key}"),
            client_hash=digest(client_id), scheduled_at=now + self.config.delay_seconds,
        )
        fingerprint = digest(f"{example_id}:{name}:{destination}")
        return await self.store.schedule(request, fingerprint=fingerprint)


class VoiceDispatcher:
    def __init__(self, store: VoiceStore, transport: TwilioVoiceTransport) -> None:
        self.store, self.transport = store, transport

    async def run_once(self, now_seconds: int | None = None) -> bool:
        now = int(time.time()) if now_seconds is None else now_seconds
        request = await self.store.claim_due(now)
        if request is None:
            return False
        result = await self.transport.submit(request)
        sid_hash = hashlib.sha256(result.call_sid.encode()).hexdigest() if result.call_sid else ""
        await self.store.finalize_submission(request, result.state, sid_hash)
        return True


CALLBACK_STATES = {
    "queued": VoiceState.PROVIDER_SUBMITTED,
    "initiated": VoiceState.PROVIDER_SUBMITTED,
    "ringing": VoiceState.RINGING,
    "in-progress": VoiceState.ANSWERED,
    "completed": VoiceState.COMPLETED,
    "busy": VoiceState.BUSY,
    "no-answer": VoiceState.NO_ANSWER,
    "failed": VoiceState.FAILED,
    "canceled": VoiceState.CANCELED,
}
