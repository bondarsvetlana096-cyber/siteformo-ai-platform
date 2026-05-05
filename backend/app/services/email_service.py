import os
from typing import Any, Dict, Iterable, Optional

import requests

RESEND_API_KEY = os.getenv("RESEND_API_KEY")
EMAIL_FROM = os.getenv("EMAIL_FROM", "SiteFormo <hello@siteformo.com>")
FRONTEND_URL = os.getenv("FRONTEND_URL", "https://siteformo.com").rstrip("/")


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
    )


# 🔥 ДОБАВЛЕНО — ЧТО ЛОМАЛО СЕРВЕР
class OwnerEmailComposer:
    @staticmethod
    def compose_order_email(order):
        order_id = _safe_get(order, "id")
        client_email = _safe_get(order, "email") or _safe_get(order, "client_email")
        client_name = _safe_get(order, "client_name") or "Client"
        total = _safe_get(order, "total_amount") or _safe_get(order, "estimated_price_eur") or 0

        html = f"""
        <div style="font-family:Arial,sans-serif;line-height:1.6;color:#111;">
            <h2>New SiteFormo order</h2>

            <p><strong>Order ID:</strong> {order_id}</p>
            <p><strong>Client:</strong> {client_name}</p>
            <p><strong>Email:</strong> {client_email}</p>
            <p><strong>Total:</strong> €{total}</p>

            <p>Please review this order.</p>
        </div>
        """

        return {
            "to": os.getenv("OWNER_EMAIL", "klon97048@gmail.com"),
            "subject": f"New order {order_id}",
            "html": html,
        }


def send_design_previews_email(order: Any, previews: Iterable[Dict[str, Any]]):
    if not RESEND_API_KEY:
        raise ValueError("RESEND_API_KEY is missing")

    client_email = (
        _safe_get(order, "client_email")
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
    <div style="font-family:Arial,sans-serif;line-height:1.6;color:#111;">
        <h2>Your design options are ready</h2>

        <p>Please open the link below and choose one design.</p>

        <p>
            <a href="{select_url}"
               style="padding:12px 20px;background:#4f46e5;color:white;text-decoration:none;border-radius:8px;">
                View designs
            </a>
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


async def send_email(to: str, subject: str, html: str):
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