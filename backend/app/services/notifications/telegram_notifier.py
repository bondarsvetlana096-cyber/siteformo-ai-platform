import os

import httpx

from app.core.settings import settings
from app.services.logging.safe_logger import get_logger, mask_sensitive

logger = get_logger("siteformo.notifications")


def format_lead_notification(lead_data: dict, user_id: str, channel: str | None, raw_text: str) -> str:
    return (
        "🚀 Новый лид Siteformo\n\n"
        f"Канал: {channel or '-'}\n"
        f"User: {mask_sensitive(user_id)}\n"
        f"Услуга: {lead_data.get('service') or '-'}\n"
        f"Город: {lead_data.get('city') or '-'}\n"
        f"Срочность: {lead_data.get('urgency') or '-'}\n"
        f"Контакт: {mask_sensitive(str(lead_data.get('contact') or '-'))}\n\n"
        f"Сообщение: {mask_sensitive(raw_text[:500])}"
    )


async def notify_owner_about_lead(lead_data: dict, user_id: str, channel: str | None, raw_text: str) -> None:
    if not settings.ENABLE_OWNER_NOTIFICATIONS:
        return

    bot_token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = settings.OWNER_TELEGRAM_CHAT_ID

    if not bot_token or not chat_id:
        return

    useful = any(lead_data.get(k) for k in ["service", "city", "urgency", "contact"])
    if not useful:
        return

    text = format_lead_notification(lead_data, user_id, channel, raw_text)

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            await client.post(
                f"https://api.telegram.org/bot{bot_token}/sendMessage",
                json={"chat_id": chat_id, "text": text},
            )
    except Exception as exc:
        logger.warning("owner_notification_failed error=%s", mask_sensitive(str(exc)))
