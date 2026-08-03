from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Protocol

import httpx

from app.services.telegram_delivery.models import TelegramMessage


class TransportState(StrEnum):
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    AUTHENTICATION_ERROR = "authentication_error"
    INVALID_CHAT = "invalid_chat"
    RATE_LIMITED = "rate_limited"
    TRANSIENT_FAILURE = "transient_failure"
    TIMEOUT = "timeout"
    AMBIGUOUS = "ambiguous"


@dataclass(frozen=True, slots=True)
class TransportResult:
    state: TransportState
    provider_message_id: str | None = None
    transport_invoked: bool = True
    http_status: int | None = None


class TelegramTransport(Protocol):
    async def send(self, message: TelegramMessage) -> TransportResult: ...


@dataclass(frozen=True, slots=True)
class TelegramTransportConfig:
    bot_token: str = field(repr=False)
    timeout_seconds: float = 10.0

    def validate(self) -> None:
        if not self.bot_token or not (1.0 <= self.timeout_seconds <= 30.0):
            raise ValueError("telegram_transport_not_configured")


class BotApiTelegramTransport:
    API_ORIGIN = "https://api.telegram.org"

    def __init__(self, config: TelegramTransportConfig, client: httpx.AsyncClient) -> None:
        config.validate()
        self.config, self.client = config, client

    async def send(self, message: TelegramMessage) -> TransportResult:
        if not isinstance(message.private_chat_id, int) or message.private_chat_id <= 0 or not message.text:
            return TransportResult(TransportState.INVALID_CHAT, transport_invoked=False)
        try:
            response = await self.client.post(
                f"{self.API_ORIGIN}/bot{self.config.bot_token}/sendMessage",
                json={"chat_id": message.private_chat_id, "text": message.text},
                timeout=self.config.timeout_seconds,
            )
        except httpx.TimeoutException:
            return TransportResult(TransportState.AMBIGUOUS)
        except httpx.HTTPError:
            return TransportResult(TransportState.TRANSIENT_FAILURE)
        if response.status_code == 200:
            try:
                body = response.json()
                message_id = body.get("result", {}).get("message_id") if body.get("ok") is True else None
            except (ValueError, AttributeError):
                message_id = None
            return (
                TransportResult(TransportState.ACCEPTED, str(message_id), http_status=200)
                if isinstance(message_id, int)
                else TransportResult(TransportState.AMBIGUOUS, http_status=200)
            )
        if response.status_code in {401, 403}:
            state = TransportState.AUTHENTICATION_ERROR
        elif response.status_code == 429:
            state = TransportState.RATE_LIMITED
        elif response.status_code >= 500:
            state = TransportState.TRANSIENT_FAILURE
        else:
            state = TransportState.REJECTED
        return TransportResult(state, http_status=response.status_code)
