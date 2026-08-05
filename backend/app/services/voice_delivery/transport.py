from __future__ import annotations

from dataclasses import dataclass, field

import httpx

from app.services.voice_delivery.models import ProviderResult, VoiceRequest, VoiceState
from app.services.voice_delivery.twiml import render_twiml


@dataclass(frozen=True, slots=True)
class TwilioVoiceTransportConfig:
    account_sid: str
    auth_token: str = field(repr=False)
    caller_e164: str
    status_callback_url: str
    voice: str
    language: str
    timeout_seconds: float = 10.0


class TwilioVoiceTransport:
    def __init__(self, config: TwilioVoiceTransportConfig, client: httpx.AsyncClient) -> None:
        self.config, self.client = config, client

    async def submit(self, request: VoiceRequest) -> ProviderResult:
        form = {
            "To": request.phone_e164,
            "From": self.config.caller_e164,
            "Twiml": render_twiml(request.first_name, voice=self.config.voice, language=self.config.language),
            "StatusCallback": self.config.status_callback_url,
            "StatusCallbackMethod": "POST",
            "StatusCallbackEvent": "initiated ringing answered completed",
            "Timeout": "20",
        }
        url = f"https://api.twilio.com/2010-04-01/Accounts/{self.config.account_sid}/Calls.json"
        try:
            response = await self.client.post(
                url, data=form, auth=(self.config.account_sid, self.config.auth_token),
                timeout=self.config.timeout_seconds,
            )
        except httpx.TimeoutException:
            return ProviderResult(VoiceState.TIMEOUT_QUARANTINED)
        except httpx.HTTPError:
            return ProviderResult(VoiceState.TIMEOUT_QUARANTINED)
        if 200 <= response.status_code < 300:
            try:
                sid = response.json().get("sid")
            except (ValueError, AttributeError):
                sid = None
            if isinstance(sid, str) and sid.startswith("CA") and len(sid) == 34:
                return ProviderResult(VoiceState.PROVIDER_SUBMITTED, response.status_code, sid)
            return ProviderResult(VoiceState.TIMEOUT_QUARANTINED, response.status_code)
        return ProviderResult(VoiceState.PROVIDER_REJECTED, response.status_code)
