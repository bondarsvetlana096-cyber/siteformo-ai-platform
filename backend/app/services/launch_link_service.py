from __future__ import annotations

from urllib.parse import quote

from app.core.config import settings


class LaunchLinkService:
    @staticmethod
    def _normalized_bypass_emails() -> set[str]:
        raw_items = [settings.payment_approval_bypass_emails or '', settings.owner_email or '']
        raw = ','.join(raw_items)
        return {item.strip().lower() for item in raw.split(',') if item.strip()}

    @staticmethod
    def should_bypass_payment_approval(email: str | None) -> bool:
        if not email:
            return False
        return email.strip().lower() in LaunchLinkService._normalized_bypass_emails()

    @staticmethod
    def whatsapp_prefill_text() -> str:
        return (
            'Hello SiteFormo, I want to start a short website brief. '
            'Please help me continue the website order process.'
        )

    @staticmethod
    def telegram_start_payload() -> str:
        return 'siteformo_intake'

    @staticmethod
    def telegram_start_hint() -> str:
        return 'Press Start and the bot will open the short website questionnaire.'

    @staticmethod
    def build_launch_links() -> dict:
        whatsapp_link = None
        if settings.whatsapp_public_number:
            phone = ''.join(ch for ch in settings.whatsapp_public_number if ch.isdigit())
            whatsapp_link = f'https://wa.me/{phone}?text={quote(LaunchLinkService.whatsapp_prefill_text())}'

        telegram_link = None
        if settings.telegram_bot_username:
            telegram_link = (
                f'https://t.me/{settings.telegram_bot_username}?start='
                f'{LaunchLinkService.telegram_start_payload()}'
            )

        return {
            'whatsapp_link': whatsapp_link,
            'telegram_link': telegram_link,
            'whatsapp_prefill_text': LaunchLinkService.whatsapp_prefill_text(),
            'telegram_start_hint': LaunchLinkService.telegram_start_hint(),
        }
