from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol


class ClaimKind(StrEnum):
    ACQUIRED = "acquired"
    REPLAY_ACCEPTED = "replay_accepted"
    REPLAY_QUARANTINED = "replay_quarantined"
    IN_PROGRESS = "in_progress"
    QUOTA_EXHAUSTED = "quota_exhausted"
    RATE_LIMITED = "rate_limited"
    CONFLICT = "conflict"


@dataclass(frozen=True, slots=True)
class DeliveryIdentity:
    channel: str
    example_id: str
    recipient_hash: str
    idempotency_hash: str
    fingerprint: str
    client_hash: str


@dataclass(frozen=True, slots=True)
class Claim:
    kind: ClaimKind
    provider_message_id: str | None = None
    remaining_deliveries: int | None = None
    failure_code: str | None = None


class DeliveryState(Protocol):
    async def claim(self, identity: DeliveryIdentity) -> Claim: ...

    async def accept(self, identity: DeliveryIdentity, provider_message_id: str) -> int: ...

    async def release(self, identity: DeliveryIdentity, failure_code: str) -> None: ...

    async def quarantine(self, identity: DeliveryIdentity, failure_code: str) -> None: ...
