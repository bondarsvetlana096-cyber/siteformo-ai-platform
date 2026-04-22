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


def build_followup_message(req: Request, reason: str) -> tuple[str, str, str, str]:
    if reason == FOLLOWUP_REASON_DEMO_READY:
        subject = "Your Siteformo demo is ready"
        headline = "Your demo page is ready"
        cta_url = req.demo_url or build_main_site_url(req)
        text = (
            "Your demo page is ready. "
            "Open it, review the result, and return to the main site to continue your order."
        )
        return subject, headline, cta_url, text

    if reason == FOLLOWUP_REASON_DEMO_CTA:
        subject = "Return to your Siteformo flow"
        headline = "You viewed the demo but did not continue"
        cta_url = build_main_site_url(req)
        text = (
            "You already viewed the demo page, but did not finish the next step. "
            "Return to the main site, complete the form, and continue your order."
        )
        return subject, headline, cta_url, text

    subject = "Complete your Siteformo request"
    headline = "You did not finish the form or payment"
    cta_url = build_main_site_url(req, step="checkout")
    text = (
        "You started the order flow but did not complete it. "
        "Return to the main site, finish the form, and complete the payment."
    )
    return subject, headline, cta_url, text


def build_outbound_followup_text(req: Request, reason: str) -> str:
    _, headline, cta_url, text = build_followup_message(req, reason)
    channel_hint = ""
    if req.contact_type == ContactType.TELEGRAM:
        channel_hint = " Reply in Telegram if you would like help finishing your request."
    elif req.contact_type == ContactType.WHATSAPP:
        channel_hint = " Reply in WhatsApp if you would like help finishing your request."
    elif req.contact_type == ContactType.MESSENGER:
        channel_hint = " Reply in Messenger if you would like help finishing your request."
    return f"{headline}. {text} Link: {cta_url}.{channel_hint}"
