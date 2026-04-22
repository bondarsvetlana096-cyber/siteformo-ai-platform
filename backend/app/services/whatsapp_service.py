from __future__ import annotations

import json
from urllib import request

from app.core.config import settings


class WhatsAppService:
    @staticmethod
    def is_configured() -> bool:
        return bool(settings.whatsapp_api_key and settings.whatsapp_phone_number_id)

    @staticmethod
    def send_text(to: str, text: str) -> None:
        if not WhatsAppService.is_configured():
            return
        payload = {
            'messaging_product': 'whatsapp',
            'to': to,
            'type': 'text',
            'text': {'body': text},
        }
        req = request.Request(
            f'https://graph.facebook.com/v23.0/{settings.whatsapp_phone_number_id}/messages',
            data=json.dumps(payload).encode('utf-8'),
            headers={
                'Authorization': f'Bearer {settings.whatsapp_api_key}',
                'Content-Type': 'application/json',
            },
            method='POST',
        )
        with request.urlopen(req, timeout=20):
            pass
