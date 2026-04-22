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
            print('NO TELEGRAM TOKEN')
            return

        url = f'https://api.telegram.org/bot{settings.telegram_bot_token}/sendMessage'

        payload = json.dumps({
            'chat_id': chat_id,
            'text': text,
        }).encode('utf-8')

        req = request.Request(
            url,
            data=payload,
            headers={'Content-Type': 'application/json'},
            method='POST',
        )

        try:
            with request.urlopen(req, timeout=20) as response:
                print('MESSAGE SENT', response.read())
        except Exception as e:
            print('TELEGRAM SEND ERROR:', e)
