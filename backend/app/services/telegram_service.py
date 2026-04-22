from __future__ import annotations

import json
from urllib import request

from app.core.config import settings


class TelegramService:
    @staticmethod
    def is_configured() -> bool:
        return bool(settings.telegram_bot_token)

    @staticmethod
    def send_text(chat_id: str | int, text: str) -> None:
        if not settings.telegram_bot_token:
            return
        payload = json.dumps({'chat_id': chat_id, 'text': text}).encode('utf-8')
        req = request.Request(
            f'https://api.telegram.org/bot{settings.telegram_bot_token}/sendMessage',
            data=payload,
            headers={'Content-Type': 'application/json'},
            method='POST',
        )
        with request.urlopen(req, timeout=20):
            pass
