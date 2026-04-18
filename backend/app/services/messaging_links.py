from __future__ import annotations

from urllib.parse import quote

from app.core.config import settings
from app.models.request import ContactType, RequestType


def build_initial_message(request_type: str, channel: str, token: str) -> str:
    request_label = 'редизайн страницы' if request_type == RequestType.REDESIGN else 'создание страницы'
    if channel == ContactType.TELEGRAM:
        return f'Здравствуйте! Хочу получить demo на {request_label}. Код: {token}'
    if channel == ContactType.WHATSAPP:
        return f'Здравствуйте! Хочу получить demo-страницу на {request_label}. Код заявки: {token}'
    if channel == ContactType.MESSENGER:
        return f'Добрый день! Прошу подготовить demo на {request_label}. Код: {token}'
    return f'Здравствуйте! Хочу получить demo. Код: {token}'


def build_result_message(demo_url: str) -> str:
    return (
        'Ваш demo готов. '
        f'Ссылка активна {settings.demo_ttl_minutes} минут: {demo_url} '
        'Если хотите сохранить и доработать страницу, ответьте на это сообщение.'
    )


def build_confirmation_link(channel: str, message: str, token: str) -> str | None:
    encoded_message = quote(message)
    if channel == ContactType.TELEGRAM:
        if not settings.telegram_bot_username:
            return None
        return f'https://t.me/{settings.telegram_bot_username}?start={quote(token)}'
    if channel == ContactType.WHATSAPP:
        if not settings.whatsapp_contact_number:
            return None
        phone = ''.join(ch for ch in settings.whatsapp_contact_number if ch.isdigit())
        return f'https://wa.me/{phone}?text={encoded_message}'
    if channel == ContactType.MESSENGER:
        if not settings.messenger_contact_url:
            return None
        separator = '&' if '?' in settings.messenger_contact_url else '?'
        return f'{settings.messenger_contact_url}{separator}ref={quote(token)}'
    return None


def build_channel_contact_label(channel: str) -> str | None:
    if channel == ContactType.TELEGRAM:
        return settings.telegram_contact_label or settings.telegram_bot_username or None
    if channel == ContactType.WHATSAPP:
        return settings.whatsapp_contact_number or None
    if channel == ContactType.MESSENGER:
        return settings.messenger_contact_label or settings.messenger_contact_url or None
    return None
