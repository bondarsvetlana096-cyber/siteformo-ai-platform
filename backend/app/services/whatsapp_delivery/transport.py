from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from enum import StrEnum
from types import MappingProxyType
from typing import Mapping, Protocol

import httpx

from app.services.whatsapp_delivery.models import WhatsAppMessage, normalize_e164, twilio_whatsapp_destination

ACCOUNT_SID = re.compile(r"^AC[0-9a-fA-F]{32}$")
MESSAGE_SID = re.compile(r"^(SM|MM)[0-9a-fA-F]{32}$")
MESSAGING_SERVICE_SID = re.compile(r"^MG[0-9a-fA-F]{32}$")
CONTENT_SID = re.compile(r"^HX[0-9a-fA-F]{32}$")


class MessageMode(StrEnum):
    SESSION_FREEFORM_BODY = "session_freeform_body"
    BUSINESS_INITIATED_TEMPLATE = "business_initiated_template"


class TransportState(StrEnum):
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    AUTHENTICATION_ERROR = "authentication_error"
    CONFIGURATION_ERROR = "configuration_error"
    INVALID_DESTINATION = "invalid_destination"
    RATE_LIMITED = "rate_limited"
    TRANSIENT_FAILURE = "transient_failure"
    TIMEOUT = "timeout"
    AMBIGUOUS_ACCEPTANCE = "ambiguous_acceptance"


@dataclass(frozen=True, slots=True)
class TransportDiagnostic:
    classification: str
    http_status: int | None = None
    provider_error_code: int | None = None


@dataclass(frozen=True, slots=True)
class TransportResult:
    state: TransportState
    provider_message_id: str | None = None
    transport_invoked: bool = True
    diagnostic: TransportDiagnostic | None = None


class WhatsAppTransport(Protocol):
    async def send(self, message: WhatsAppMessage, provider_correlation_key: str) -> TransportResult: ...


class FakeTwilioWhatsAppTransport:
    def __init__(self, state: TransportState = TransportState.ACCEPTED) -> None:
        self.state = state
        self.calls: list[tuple[WhatsAppMessage, str]] = []
        self._keys: set[str] = set()

    async def send(self, message: WhatsAppMessage, provider_correlation_key: str) -> TransportResult:
        if provider_correlation_key in self._keys:
            raise RuntimeError("duplicate_transport_invocation")
        self._keys.add(provider_correlation_key)
        self.calls.append((message, provider_correlation_key))
        invoked = self.state is not TransportState.TIMEOUT
        return TransportResult(
            self.state,
            "SM" + "0" * 32 if self.state is TransportState.ACCEPTED else None,
            transport_invoked=invoked,
            diagnostic=TransportDiagnostic(self.state.value),
        )


@dataclass(frozen=True, slots=True)
class TwilioConfig:
    account_sid: str
    auth_token: str = field(repr=False)
    message_mode: MessageMode
    sender_e164: str | None = None
    messaging_service_sid: str | None = None
    content_sid: str | None = None
    status_callback_url: str | None = None

    def validate(self) -> None:
        if not ACCOUNT_SID.fullmatch(self.account_sid) or not self.auth_token:
            raise ValueError("provider_not_configured")
        if self.messaging_service_sid:
            if not MESSAGING_SERVICE_SID.fullmatch(self.messaging_service_sid):
                raise ValueError("invalid_messaging_service_sid")
        elif self.sender_e164:
            normalize_e164(self.sender_e164)
        else:
            raise ValueError("twilio_sender_not_configured")
        if self.message_mode is MessageMode.BUSINESS_INITIATED_TEMPLATE:
            if not self.content_sid or not CONTENT_SID.fullmatch(self.content_sid):
                raise ValueError("content_sid_required")
        elif self.content_sid:
            raise ValueError("content_sid_conflicts_with_freeform")
        if self.status_callback_url and not self.status_callback_url.startswith("https://"):
            raise ValueError("invalid_status_callback_url")

    @property
    def sender_mode(self) -> str:
        return "messaging_service" if self.messaging_service_sid else "direct_sender"


class TwilioWhatsAppTransport:
    """Injected-client candidate; it has no environment or browser configuration access."""

    API_ORIGIN = "https://api.twilio.com"

    def __init__(self, config: TwilioConfig, client: httpx.AsyncClient) -> None:
        config.validate()
        self.config = config
        self.client = client

    @property
    def endpoint(self) -> str:
        return f"{self.API_ORIGIN}/2010-04-01/Accounts/{self.config.account_sid}/Messages.json"

    def form(self, message: WhatsAppMessage) -> Mapping[str, str]:
        form: dict[str, str] = {"To": twilio_whatsapp_destination(message.destination_e164)}
        if self.config.messaging_service_sid:
            form["MessagingServiceSid"] = self.config.messaging_service_sid
        elif self.config.sender_e164:
            form["From"] = twilio_whatsapp_destination(self.config.sender_e164)
        if self.config.message_mode is MessageMode.SESSION_FREEFORM_BODY:
            form["Body"] = message.body
        else:
            form["ContentSid"] = self.config.content_sid or ""
            form["ContentVariables"] = json.dumps(
                dict(message.content_variables),
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
            )
        if self.config.status_callback_url:
            form["StatusCallback"] = self.config.status_callback_url
        return MappingProxyType(form)

    async def send(self, message: WhatsAppMessage, provider_correlation_key: str) -> TransportResult:
        try:
            response = await self.client.post(
                self.endpoint,
                data=self.form(message),
                auth=(self.config.account_sid, self.config.auth_token),
            )
        except httpx.TimeoutException:
            return TransportResult(
                TransportState.AMBIGUOUS_ACCEPTANCE,
                diagnostic=TransportDiagnostic("request_timeout_after_dispatch"),
            )
        except httpx.HTTPError:
            return TransportResult(
                TransportState.TRANSIENT_FAILURE,
                diagnostic=TransportDiagnostic("transport_error"),
            )
        return self._map_response(response, provider_correlation_key)

    @staticmethod
    def _safe_body(response: httpx.Response) -> tuple[str | None, int | None]:
        try:
            body = response.json()
        except ValueError:
            return None, None
        if not isinstance(body, dict):
            return None, None
        sid = body.get("sid")
        code = body.get("code")
        return (sid if isinstance(sid, str) else None, code if isinstance(code, int) else None)

    def _map_response(self, response: httpx.Response, _: str) -> TransportResult:
        sid, error_code = self._safe_body(response)
        diagnostic = TransportDiagnostic("provider_response", response.status_code, error_code)
        if response.status_code in {200, 201}:
            if sid and MESSAGE_SID.fullmatch(sid):
                return TransportResult(TransportState.ACCEPTED, sid, diagnostic=diagnostic)
            return TransportResult(TransportState.AMBIGUOUS_ACCEPTANCE, diagnostic=diagnostic)
        if response.status_code in {401, 403}:
            state = TransportState.AUTHENTICATION_ERROR
        elif response.status_code == 429:
            state = TransportState.RATE_LIMITED
        elif response.status_code in {404, 405}:
            state = TransportState.CONFIGURATION_ERROR
        elif error_code in {21211, 21614, 63007, 63016}:
            state = TransportState.INVALID_DESTINATION
        elif response.status_code >= 500:
            state = TransportState.TRANSIENT_FAILURE
        else:
            state = TransportState.REJECTED
        return TransportResult(state, diagnostic=diagnostic)
