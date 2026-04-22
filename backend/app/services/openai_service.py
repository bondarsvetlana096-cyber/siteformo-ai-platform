from __future__ import annotations

import json
from urllib import error, request

from app.core.config import settings


class OpenAIService:
    @staticmethod
    def is_configured() -> bool:
        return bool(settings.openai_api_key)

    @staticmethod
    def refine_reply(system_prompt: str, user_text: str, fallback_text: str) -> str:
        if not settings.openai_api_key:
            return fallback_text

        payload = {
            'model': settings.openai_model,
            'input': [
                {'role': 'system', 'content': [{'type': 'input_text', 'text': system_prompt}]},
                {'role': 'user', 'content': [{'type': 'input_text', 'text': user_text}]},
            ],
            'text': {'format': {'type': 'text'}},
        }
        req = request.Request(
            'https://api.openai.com/v1/responses',
            data=json.dumps(payload).encode('utf-8'),
            headers={
                'Authorization': f'Bearer {settings.openai_api_key}',
                'Content-Type': 'application/json',
            },
            method='POST',
        )
        try:
            with request.urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read().decode('utf-8'))
                return data.get('output_text') or fallback_text
        except (error.URLError, error.HTTPError, TimeoutError, json.JSONDecodeError):
            return fallback_text
