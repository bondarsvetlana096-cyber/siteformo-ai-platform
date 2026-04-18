from __future__ import annotations

import logging

import resend

from app.core.config import settings

logger = logging.getLogger("siteformo.email")


def _wrap_email(title: str, body_text: str, cta_label: str, cta_url: str, footer_text: str) -> str:
    return f"""<!doctype html>
<html>
  <body style="margin:0;padding:0;background:#0b1020;font-family:Inter,Arial,sans-serif;color:#ffffff;">
    <div style="max-width:640px;margin:0 auto;padding:32px 20px;">
      <div style="background:linear-gradient(135deg,#121936,#0d1328);border:1px solid rgba(255,255,255,.1);border-radius:28px;padding:32px;box-shadow:0 20px 60px rgba(0,0,0,.24);">
        <div style="display:inline-block;padding:8px 14px;border-radius:999px;border:1px solid rgba(255,255,255,.14);background:rgba(255,255,255,.04);font-size:12px;font-weight:700;letter-spacing:.08em;text-transform:uppercase;">Siteformo</div>
        <h1 style="font-size:34px;line-height:1.08;margin:18px 0 12px;">{title}</h1>
        <p style="font-size:17px;line-height:1.7;color:#d5dcf5;margin:0 0 18px;">{body_text}</p>
        <p style="margin:24px 0 26px;">
          <a href="{cta_url}" style="display:inline-block;padding:16px 24px;border-radius:16px;background:linear-gradient(90deg,#7c3aed,#06b6d4);color:#fff;text-decoration:none;font-weight:800;">
            {cta_label}
          </a>
        </p>
        <p style="font-size:14px;line-height:1.7;color:#9fb0df;margin:0;">{footer_text}</p>
      </div>
    </div>
  </body>
</html>"""


async def send_demo_email(to_email: str, subject: str, title: str, body_text: str, cta_label: str, cta_url: str, footer_text: str) -> None:
    logger.info("[EMAIL] preparing email: %s subject=%s", to_email, subject)
    if not settings.resend_api_key:
        logger.warning("[EMAIL] resend api key is missing, skip send")
        return

    resend.api_key = settings.resend_api_key
    resend.Emails.send(
        {
            "from": settings.resend_from_email,
            "to": [to_email],
            "subject": subject,
            "html": _wrap_email(title, body_text, cta_label, cta_url, footer_text),
        }
    )
    logger.info("[EMAIL] sent to %s", to_email)
