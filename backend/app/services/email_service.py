from __future__ import annotations

import logging
import smtplib
from email.mime.text import MIMEText

from app.core.config import settings
from app.services.approval_service import ApprovalService

logger = logging.getLogger("siteformo.email")


async def send_email(to: str | None, subject: str, html: str, text: str | None = None) -> bool:
    if not to:
        logger.warning("Email skipped because recipient is empty: subject=%s", subject)
        return False

    sender = settings.smtp_from or settings.smtp_user or settings.owner_email or "no-reply@siteformo.com"
    if not settings.smtp_host or not settings.smtp_user or not settings.smtp_password:
        logger.warning("SMTP is not configured. Email would be sent to %s: %s\n%s", to, subject, html)
        return False

    msg = MIMEText(html if html else (text or ""), "html", "utf-8")
    msg["Subject"] = subject
    msg["From"] = sender
    msg["To"] = to

    with smtplib.SMTP(settings.smtp_host, settings.smtp_port or 587, timeout=20) as smtp:
        smtp.starttls()
        smtp.login(settings.smtp_user, settings.smtp_password)
        smtp.sendmail(sender, [to], msg.as_string())
    return True


async def send_demo_email(
    to: str,
    subject: str,
    title: str,
    body_text: str,
    cta_label: str | None = None,
    cta_url: str | None = None,
    footer_text: str | None = None,
) -> bool:
    cta = f'<p><a href="{cta_url}" style="display:inline-block;padding:12px 16px;background:#111827;color:#fff;text-decoration:none;border-radius:10px">{cta_label or "Open"}</a></p>' if cta_url else ""
    html = f"""
    <h1>{title}</h1>
    <p>{body_text}</p>
    {cta}
    <p style="color:#666;font-size:12px">{footer_text or ''}</p>
    """
    return await send_email(to, subject, html, body_text)


class OwnerEmailComposer:
    @staticmethod
    def compose_order_email(order) -> dict:
        approve_url = ApprovalService.build_action_url(order.id, 'approve')
        reject_url = ApprovalService.build_action_url(order.id, 'reject')
        return {
            'to': settings.owner_email,
            'subject': f'Payment approval required: {order.business_name or order.id}',
            'html': f'''
                <h1>SiteFormo payment approval required</h1>
                <p><b>Order ID:</b> {order.id}</p>
                <p><b>Business:</b> {order.business_name or '-'}</p>
                <p><b>Tier:</b> {order.recommended_tier}</p>
                <p><b>Estimated full price:</b> €{order.estimated_price_eur}</p>
                <p><b>50% deposit:</b> €{int(order.estimated_price_eur / 2)}</p>
                <p><b>Reasoning:</b> {order.pricing_reasoning or '-'}</p>
                <p><b>Contact:</b> {getattr(order.client, 'email', None) or getattr(order.client, 'phone', None) or getattr(order.client, 'telegram_handle', None) or '-'}</p>
                <p><a href="{approve_url}">Approve generation</a></p>
                <p><a href="{reject_url}">Reject / hold</a></p>
            ''',
        }

    @staticmethod
    def compose_delivery_email(order, brief_markdown: str) -> dict:
        concepts = ''.join(
            f'<h2>Concept {c.concept_label}</h2><p>{c.summary or ""}</p><pre>{c.html_code}</pre>'
            for c in getattr(order, 'concepts', [])
        )
        return {
            'to': settings.owner_email,
            'subject': f'Divi 5 package ready: {order.business_name or order.id}',
            'html': f'''
                <h1>Final SiteFormo package ready</h1>
                <p><b>Order ID:</b> {order.id}</p>
                <p><b>Client contact:</b> {getattr(order.client, 'email', None) or getattr(order.client, 'phone', None) or getattr(order.client, 'telegram_handle', None) or '-'}</p>
                <p><b>Topic / source:</b> {order.source_url or order.desired_site_description or '-'}</p>
                <p><b>Project type:</b> {'Redesign' if order.source_url else 'New site'}</p>
                <h2>Extended brief</h2><pre>{brief_markdown}</pre>
                <h2>Two Divi-ready samples</h2>{concepts}
            ''',
        }
