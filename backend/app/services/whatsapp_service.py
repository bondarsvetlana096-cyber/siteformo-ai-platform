from __future__ import annotations

import json
from html import escape
from urllib import request

from app.core.config import settings


class WhatsAppService:
    @staticmethod
    def provider() -> str:
        return (settings.whatsapp_provider or "twilio").strip().lower()

    @staticmethod
    def is_configured() -> bool:
        provider = WhatsAppService.provider()
        if provider == "meta":
            return bool(settings.whatsapp_api_key and settings.whatsapp_phone_number_id)
        if provider == "twilio":
            return True
        return False

    @staticmethod
    def build_twiml(text: str) -> str:
        safe_text = escape(text or "")
        return f'<?xml version="1.0" encoding="UTF-8"?><Response><Message>{safe_text}</Message></Response>'

    @staticmethod
    def send_text(to: str, text: str) -> None:
        if WhatsAppService.provider() != "meta":
            return
        if not WhatsAppService.is_configured():
            return

        payload = {
            "messaging_product": "whatsapp",
            "to": to,
            "type": "text",
            "text": {"body": text},
        }
        req = request.Request(
            f"https://graph.facebook.com/v23.0/{settings.whatsapp_phone_number_id}/messages",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {settings.whatsapp_api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        with request.urlopen(req, timeout=20):
            pass
