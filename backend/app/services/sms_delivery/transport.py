from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Protocol

import httpx

from app.services.sms_delivery.models import SmsMessage

MESSAGE_SID = re.compile(r"^(SM|MM)[0-9a-fA-F]{32}$")


class SmsTransportOutcome(StrEnum):
    ACCEPTED = "ACCEPTED"
    REJECTED = "REJECTED"
    AMBIGUOUS = "AMBIGUOUS"
    QUARANTINED = "QUARANTINED"
    CONFIGURATION_ERROR = "CONFIGURATION_ERROR"


@dataclass(frozen=True, slots=True)
class SmsTransportResult:
    outcome: SmsTransportOutcome
    provider_message_sid: str | None = field(default=None, repr=False)
    http_status: int | None = None
    transport_invoked: bool = True


class SmsTransport(Protocol):
    async def send(self, message: SmsMessage, correlation_key: str) -> SmsTransportResult: ...


class TwilioSmsTransport:
    API_ORIGIN = "https://api.twilio.com"

    def __init__(self, *, account_sid: str, auth_token: str, sender_e164: str, client: httpx.AsyncClient) -> None:
        self.account_sid = account_sid
        self._auth_token = auth_token
        self.sender_e164 = sender_e164
        self.client = client

    @property
    def endpoint(self) -> str:
        return f"{self.API_ORIGIN}/2010-04-01/Accounts/{self.account_sid}/Messages.json"

    async def send(self, message: SmsMessage, correlation_key: str) -> SmsTransportResult:
        del correlation_key
        try:
            response = await self.client.post(
                self.endpoint,
                data={"From": self.sender_e164, "To": message.destination_e164, "Body": message.body},
                auth=(self.account_sid, self._auth_token),
            )
        except httpx.TimeoutException:
            return SmsTransportResult(SmsTransportOutcome.QUARANTINED)
        except httpx.HTTPError:
            return SmsTransportResult(SmsTransportOutcome.QUARANTINED)
        try:
            body = response.json()
        except ValueError:
            body = None
        sid = body.get("sid") if isinstance(body, dict) else None
        if 200 <= response.status_code < 300:
            if isinstance(sid, str) and MESSAGE_SID.fullmatch(sid):
                return SmsTransportResult(SmsTransportOutcome.ACCEPTED, sid, response.status_code)
            return SmsTransportResult(SmsTransportOutcome.AMBIGUOUS, http_status=response.status_code)
        if response.status_code in {401, 403, 404, 405}:
            outcome = SmsTransportOutcome.CONFIGURATION_ERROR
        else:
            outcome = SmsTransportOutcome.REJECTED
        return SmsTransportResult(outcome, http_status=response.status_code)
