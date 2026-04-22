from __future__ import annotations

from urllib.parse import quote

from app.core.config import settings
from app.models.request import ContactType, RequestType


def build_initial_message(request_type: str, channel: str, token: str) -> str:
    request_label = 'page redesign' if request_type == RequestType.REDESIGN else 'new page creation'
    if channel == ContactType.TELEGRAM:
        return f'Hello! I would like to receive a demo for {request_label}. Code: {token}'
    if channel == ContactType.WHATSAPP:
        return f'Hello! I would like to receive my demo for {request_label}. Code: {token}'
    if channel == ContactType.MESSENGER:
        return f'Hello! Please send my website demo for {request_label}. Code: {token}'
    return f'Hello! I would like to receive a demo. Code: {token}'


def build_result_message(demo_url: str) -> str:
    return (
        'Your demo is ready. '
        f'This link is active for {settings.demo_ttl_minutes} minutes: {demo_url} '
        'Reply to this message if you would like to save and refine the page.'
    )


def build_confirmation_link(channel: str, message: str, token: str) -> str | None:
    encoded_message = quote(message)
    if channel == ContactType.TELEGRAM:
        if not settings.telegram_bot_username:
            return None
        return f'https://t.me/{settings.telegram_bot_username}?start={quote(token)}&text={encoded_message}'
    if channel == ContactType.WHATSAPP:
        if not settings.whatsapp_contact_number:
            return None
        digits = ''.join(ch for ch in str(settings.whatsapp_contact_number) if ch.isdigit())
        if not digits:
            return None
        return f'https://wa.me/{digits}?text={encoded_message}'
    if channel == ContactType.MESSENGER:
        base = settings.messenger_contact_url
        if not base:
            return None
        separator = '&' if '?' in base else '?'
        return f'{base}{separator}ref={quote(token)}&text={encoded_message}'
    return None


def build_channel_contact_label(channel: str) -> str | None:
    if channel == ContactType.TELEGRAM:
        return settings.telegram_contact_label or settings.telegram_bot_username or None
    if channel == ContactType.WHATSAPP:
        return settings.whatsapp_contact_label or settings.whatsapp_contact_number or 'WhatsApp'
    if channel == ContactType.MESSENGER:
        return settings.messenger_contact_label or 'Facebook Messenger'
    return None
