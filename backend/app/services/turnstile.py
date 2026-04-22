from __future__ import annotations

import httpx

from app.core.config import settings


def _is_development() -> bool:
    return settings.app_env.strip().lower() in {'development', 'dev', 'local'}


async def verify_turnstile(token: str | None, remote_ip: str | None = None) -> bool:
    if _is_development():
        return True

    if not settings.turnstile_secret_key:
        return True

    if not token:
        return False

    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.post(
            'https://challenges.cloudflare.com/turnstile/v0/siteverify',
            data={
                'secret': settings.turnstile_secret_key,
                'response': token,
                'remoteip': remote_ip or '',
            },
        )
        response.raise_for_status()
        data = response.json()
        return bool(data.get('success'))