from __future__ import annotations

from urllib.parse import urlencode

from app.core.config import settings
from app.models.request import ContactType, Request


FOLLOWUP_REASON_DEMO_READY = "demo_ready"
FOLLOWUP_REASON_DEMO_CTA = "demo_cta"
FOLLOWUP_REASON_CHECKOUT = "checkout"


def build_main_site_url(req: Request, step: str = "continue") -> str:
    base = settings.main_site_base_url.rstrip("/")
    path = settings.main_site_continue_path
    if step == "checkout":
        path = settings.main_site_checkout_path
    path = "/" + path.lstrip("/")
    query = urlencode(
        {
            "request_id": str(req.id),
            "demo_token": req.demo_token or "",
            "contact_type": req.contact_type,
        }
    )
    return f"{base}{path}?{query}"


def build_followup_message(req: Request, reason: str) -> tuple[str, str, str]:
    if reason == FOLLOWUP_REASON_DEMO_READY:
        subject = "Your Siteformo demo is ready"
        headline = "Ваша demo-страница уже готова"
        cta_url = req.demo_url or build_main_site_url(req)
        text = (
            "Ваша demo-страница уже готова. "
            "Откройте её, посмотрите результат и перейдите на основной сайт, чтобы заполнить форму и продолжить оформление."
        )
        return subject, headline, cta_url, text

    if reason == FOLLOWUP_REASON_DEMO_CTA:
        subject = "Return to your Siteformo flow"
        headline = "Вы посмотрели demo, но не продолжили"
        cta_url = build_main_site_url(req)
        text = (
            "Вы уже посмотрели demo-страницу, но не завершили следующий шаг. "
            "Вернитесь на основной сайт, заполните форму и продолжите оформление."
        )
        return subject, headline, cta_url, text

    subject = "Complete your Siteformo request"
    headline = "Вы не завершили форму или оплату"
    cta_url = build_main_site_url(req, step="checkout")
    text = (
        "Вы начали оформление, но не завершили его. "
        "Вернитесь на основной сайт, заполните форму до конца и завершите оплату."
    )
    return subject, headline, cta_url, text


def build_outbound_followup_text(req: Request, reason: str) -> str:
    _, headline, cta_url, text = build_followup_message(req, reason)
    channel_hint = ""
    if req.contact_type == ContactType.TELEGRAM:
        channel_hint = " Напишите в Telegram, если хотите, чтобы мы помогли довести заявку до конца."
    elif req.contact_type == ContactType.WHATSAPP:
        channel_hint = " Ответьте в WhatsApp, если нужна помощь с завершением заявки."
    elif req.contact_type == ContactType.MESSENGER:
        channel_hint = " Ответьте в Messenger, если нужна помощь с завершением заявки."
    return f"{headline}. {text} Ссылка: {cta_url}.{channel_hint}"
