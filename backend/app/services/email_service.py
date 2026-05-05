import os
from typing import Any, Dict, Iterable, Optional

import requests

RESEND_API_KEY = os.getenv("RESEND_API_KEY")
EMAIL_FROM = os.getenv("EMAIL_FROM", "SiteFormo <hello@siteformo.com>")
FRONTEND_URL = os.getenv("FRONTEND_URL", "https://siteformo.com").rstrip("/")
OWNER_EMAIL = os.getenv("OWNER_EMAIL", "klon97048@gmail.com")


def _safe_get(obj: Any, key: str, default: Any = None) -> Any:
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _preview_image_url(preview: Any) -> Optional[str]:
    return (
        _safe_get(preview, "image_url")
        or _safe_get(preview, "preview_url")
        or _safe_get(preview, "screenshot_url")
        or _safe_get(preview, "desktop_image_url")
    )


class OwnerEmailComposer:
    @staticmethod
    def compose_order_email(order: Any) -> Dict[str, str]:
        order_id = _safe_get(order, "id") or _safe_get(order, "order_id") or ""
        client = _safe_get(order, "client")

        client_email = (
            _safe_get(client, "email")
            or _safe_get(order, "email")
            or _safe_get(order, "client_email")
            or _safe_get(order, "contact_email")
            or ""
        )

        client_name = (
            _safe_get(client, "name")
            or _safe_get(order, "client_name")
            or _safe_get(order, "name")
            or "Client"
        )

        total = (
            _safe_get(order, "estimated_price_eur")
            or _safe_get(order, "total_amount")
            or _safe_get(order, "price")
            or 0
        )

        html = f"""
        <div style="font-family:Arial,sans-serif;line-height:1.6;color:#111;">
            <h2>New SiteFormo order needs review</h2>
            <p><strong>Order ID:</strong> {order_id}</p>
            <p><strong>Client:</strong> {client_name}</p>
            <p><strong>Email:</strong> {client_email}</p>
            <p><strong>Total:</strong> €{total}</p>
            <p>Please review this order in the admin panel.</p>
        </div>
        """

        return {
            "to": OWNER_EMAIL,
            "subject": f"SiteFormo order review - {order_id}",
            "html": html,
        }


async def send_email(to: str, subject: str, html: str):
    """
    Universal email sender used by worker, request_service and order_routes.
    """
    if not RESEND_API_KEY:
        raise ValueError("RESEND_API_KEY is missing")

    response = requests.post(
        "https://api.resend.com/emails",
        headers={
            "Authorization": f"Bearer {RESEND_API_KEY}",
            "Content-Type": "application/json",
        },
        json={
            "from": EMAIL_FROM,
            "to": [to],
            "subject": subject,
            "html": html,
        },
        timeout=20,
    )

    if response.status_code >= 400:
        raise Exception(f"Resend error: {response.text}")

    return response.json()


async def send_demo_email(to: str, subject: str = "", html: str = "", **kwargs):
    """
    Backward-compatible helper required by app.services.request_service.

    Supports both:
    - await send_demo_email(to, subject, html)
    - await send_demo_email(email=..., demo_url=..., ...)
    """
    email = to or kwargs.get("email") or kwargs.get("client_email")
    if not email:
        raise ValueError("Recipient email is missing")

    demo_url = (
        kwargs.get("demo_url")
        or kwargs.get("url")
        or kwargs.get("link")
        or f"{FRONTEND_URL}/demo"
    )

    final_subject = subject or kwargs.get("subject") or "Your SiteFormo demo link"

    final_html = html or kwargs.get("html") or f"""
    <div style="font-family:Arial,sans-serif;line-height:1.6;color:#111;">
        <h2>Your SiteFormo demo is ready</h2>
        <p>Please open the link below to view your demo:</p>
        <p>
            <a href="{demo_url}"
               style="padding:12px 20px;background:#4f46e5;color:white;text-decoration:none;border-radius:8px;display:inline-block;font-weight:bold;">
                View demo
            </a>
        </p>
        <p>If the button does not work, copy this link:<br>
            <a href="{demo_url}">{demo_url}</a>
        </p>
        <p>SiteFormo Team</p>
    </div>
    """

    return await send_email(email, final_subject, final_html)


def send_design_previews_email(order: Any, previews: Iterable[Dict[str, Any]]):
    """
    Sends one button/link to the design-previews page.
    Screenshots are shown on the website, not inside the email.
    """
    if not RESEND_API_KEY:
        raise ValueError("RESEND_API_KEY is missing")

    client = _safe_get(order, "client")
    client_email = (
        _safe_get(client, "email")
        or _safe_get(order, "client_email")
        or _safe_get(order, "email")
        or _safe_get(order, "contact_email")
    )

    if not client_email:
        raise ValueError("Client email is missing")

    order_id = _safe_get(order, "id") or _safe_get(order, "order_id")
    if not order_id:
        raise ValueError("Order ID is missing")

    select_url = f"{FRONTEND_URL}/design-previews?order_id={order_id}"

    html = f"""
    <div style="font-family:Arial,sans-serif;line-height:1.6;color:#111;max-width:680px;margin:0 auto;">
        <h2>Your design options are ready</h2>

        <p>Your project brief has been received and your homepage design options are ready.</p>

        <p>
            Please open the link below and choose one design direction from the five screenshots.
        </p>

        <p>
            <a href="{select_url}"
               style="padding:12px 20px;background:#4f46e5;color:white;text-decoration:none;border-radius:8px;display:inline-block;font-weight:bold;">
                View your designs
            </a>
        </p>

        <p>
            After you select one design, your 1-hour refund window starts and full website production begins.
        </p>

        <p>If the button does not work, copy this link:<br>
            <a href="{select_url}">{select_url}</a>
        </p>

        <p>SiteFormo Team</p>
    </div>
    """

    response = requests.post(
        "https://api.resend.com/emails",
        headers={
            "Authorization": f"Bearer {RESEND_API_KEY}",
            "Content-Type": "application/json",
        },
        json={
            "from": EMAIL_FROM,
            "to": [client_email],
            "subject": "Your SiteFormo design options are ready",
            "html": html,
        },
        timeout=20,
    )

    if response.status_code >= 400:
        raise Exception(f"Resend error: {response.text}")

    return response.json()
