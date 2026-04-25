from __future__ import annotations

import os
from html import escape

import httpx

from app.core.config import settings
from app.services.approval_service import ApprovalService


RESEND_API_URL = "https://api.resend.com/emails"


def _setting(name: str, default=None):
    return os.getenv(name) or getattr(settings, name.lower(), default)


async def send_email(to: str | None, subject: str, html: str):
    api_key = os.getenv("RESEND_API_KEY")
    from_email = os.getenv("EMAIL_FROM") or "SiteFormo <hello@siteformo.com>"
    owner_email = os.getenv("OWNER_EMAIL") or getattr(
        settings,
        "owner_email",
        "klon97048@gmail.com",
    )

    if not api_key:
        raise RuntimeError("RESEND_API_KEY is not set")

    recipient = to or owner_email

    if not recipient:
        raise RuntimeError("Email recipient is not set")

    payload = {
        "from": from_email,
        "to": [recipient],
        "subject": subject,
        "html": html,
    }

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    async with httpx.AsyncClient(timeout=20) as client:
        response = await client.post(
            RESEND_API_URL,
            json=payload,
            headers=headers,
        )

    if response.status_code >= 300:
        raise RuntimeError(
            f"Resend error: {response.status_code} {response.text}"
        )

    print("EMAIL SENT ✅")

    return response.json()


class OwnerEmailComposer:
    @staticmethod
    def _owner_email() -> str:
        return os.getenv("OWNER_EMAIL") or getattr(
            settings,
            "owner_email",
            "klon97048@gmail.com",
        )

    @staticmethod
    def compose_order_email(order) -> dict:
        client = getattr(order, "client", None)

        client_email = escape(getattr(client, "email", "") or "Not provided")
        client_whatsapp = escape(getattr(client, "whatsapp", "") or "Not provided")
        client_telegram = escape(getattr(client, "telegram", "") or "Not provided")

        business_name = escape(getattr(order, "business_name", "") or "Not provided")
        source_url = escape(getattr(order, "source_url", "") or "Not provided")
        description = escape(
            getattr(order, "desired_site_description", "") or "Not provided"
        )
        tier = escape(str(getattr(order, "recommended_tier", "") or "Not provided"))
        price = escape(str(getattr(order, "estimated_price_eur", "") or "Not provided"))
        reasoning = escape(
            getattr(order, "pricing_reasoning", "") or "Not provided"
        )

        approve_url = ApprovalService.build_action_url(order.id, "approve")
        reject_url = ApprovalService.build_action_url(order.id, "reject")

        subject = f"SiteFormo payment approval required: {business_name}"

        html = f"""
        <div style="font-family:Arial,sans-serif;line-height:1.5;color:#111827;">
            <h2>New SiteFormo Order</h2>

            <h3>Client</h3>
            <p><strong>Email:</strong> {client_email}</p>
            <p><strong>WhatsApp:</strong> {client_whatsapp}</p>
            <p><strong>Telegram:</strong> {client_telegram}</p>

            <h3>Project</h3>
            <p><strong>Business name:</strong> {business_name}</p>
            <p><strong>Source URL:</strong> {source_url}</p>
            <p><strong>Description:</strong> {description}</p>

            <h3>Pricing</h3>
            <p><strong>Recommended tier:</strong> {tier}</p>
            <p><strong>Estimated price EUR:</strong> {price}</p>
            <p><strong>Reasoning:</strong> {reasoning}</p>

            <p style="margin-top:28px;">
                <a href="{approve_url}" style="background:#16a34a;color:white;padding:14px 20px;text-decoration:none;border-radius:8px;display:inline-block;font-weight:bold;">
                    APPROVE PAYMENT
                </a>

                <a href="{reject_url}" style="background:#dc2626;color:white;padding:14px 20px;text-decoration:none;border-radius:8px;display:inline-block;font-weight:bold;margin-left:10px;">
                    REJECT
                </a>
            </p>

            <p style="margin-top:24px;color:#6b7280;font-size:13px;">
                If the approve button is clicked, the client will be allowed to continue to the extended brief and final generation flow.
            </p>
        </div>
        """

        return {
            "to": OwnerEmailComposer._owner_email(),
            "subject": subject,
            "html": html,
        }

    @staticmethod
    def compose_delivery_email(order, brief_markdown: str) -> dict:
        client = getattr(order, "client", None)

        client_email = escape(getattr(client, "email", "") or "Not provided")
        business_name = escape(getattr(order, "business_name", "") or "Not provided")
        source_url = escape(getattr(order, "source_url", "") or "Not provided")
        description = escape(
            getattr(order, "desired_site_description", "") or "Not provided"
        )
        brief = escape(brief_markdown or "No extended brief provided")

        subject = f"SiteFormo final package ready: {business_name}"

        html = f"""
        <div style="font-family:Arial,sans-serif;line-height:1.5;color:#111827;">
            <h2>Final SiteFormo Package Ready</h2>

            <h3>Client</h3>
            <p><strong>Email:</strong> {client_email}</p>

            <h3>Project</h3>
            <p><strong>Business name:</strong> {business_name}</p>
            <p><strong>Source URL:</strong> {source_url}</p>
            <p><strong>Description:</strong> {description}</p>

            <h3>Extended brief</h3>
            <pre style="white-space:pre-wrap;background:#f4f4f4;padding:16px;border-radius:8px;">{brief}</pre>

            <p>The Divi 5-ready final package has been generated and is ready for visual review.</p>
        </div>
        """

        return {
            "to": OwnerEmailComposer._owner_email(),
            "subject": subject,
            "html": html,
        }