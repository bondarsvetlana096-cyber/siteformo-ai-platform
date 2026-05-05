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


def send_design_previews_email(order: Any, previews: Iterable[Dict[str, Any]]):
    """
    Sends the client the generated design preview options.

    Expected preview shape:
    {
      "id": "design_1" or DB UUID,
      "image_url": "https://.../preview.png",
      "label": "Design option 1"
    }

    Important: for best email rendering, image_url should be a real public HTTPS URL.
    generation_service.py now uploads OpenAI images to Supabase Storage when these env vars exist:
    SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY, SUPABASE_STORAGE_BUCKET.
    """
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

    select_url = f"{FRONTEND_URL}/select-design?order_id={order_id}"

    preview_blocks = ""
    preview_list = list(previews or [])

    for index, preview in enumerate(preview_list, start=1):
        image_url = _preview_image_url(preview)
        preview_id = _safe_get(preview, "id") or f"design_{index}"
        label = _safe_get(preview, "label") or f"Design option {index}"
        choose_url = f"{select_url}&preview_id={preview_id}"

        image_html = (
            f'<img src="{image_url}" style="max-width:100%;border-radius:10px;margin-bottom:16px;border:1px solid #eee;" />'
            if image_url
            else '<p style="color:#666;">Preview image is attached to your project dashboard.</p>'
        )

        preview_blocks += f"""
        <div style="margin-bottom:32px;padding:20px;border:1px solid #ddd;border-radius:12px;">
            <h3>{label}</h3>
            {image_html}
            <p>
                <a href="{choose_url}"
                   style="display:inline-block;background:#111;color:#fff;padding:12px 18px;border-radius:8px;text-decoration:none;">
                   Select this design
                </a>
            </p>
            <p style="font-size:13px;color:#666;word-break:break-all;">
                If the button does not work, copy this link:<br />
                <a href="{choose_url}">{choose_url}</a>
            </p>
        </div>
        """

    html = f"""
    <div style="font-family:Arial,sans-serif;line-height:1.6;color:#111;max-width:760px;margin:0 auto;">
        <h2>Your homepage design options are ready</h2>

        <p>Hello,</p>

        <p>
            Based on your questionnaire, our development team prepared your first design options.
            Please review them and select the one you want us to use for the final website.
        </p>

        <p><strong>You can select only one design.</strong></p>

        {preview_blocks}

        <p>
            After you select a design, the final production stage will begin.
            You will still have a 1-hour refund window after selection.
        </p>

        <p>
            You can also open all options here:<br />
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
    
    async def send_email(to: str, subject: str, html: str):
    """
    Universal email sender used by worker and other services.
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
