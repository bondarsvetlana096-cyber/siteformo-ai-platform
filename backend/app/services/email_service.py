import os
import httpx


RESEND_API_URL = "https://api.resend.com/emails"


async def send_email(to: str, subject: str, html: str):
    api_key = os.getenv("RESEND_API_KEY")
    from_email = os.getenv("EMAIL_FROM")
    owner_email = os.getenv("OWNER_EMAIL")

    if not api_key:
        raise Exception("RESEND_API_KEY not set")

    if not from_email:
        raise Exception("EMAIL_FROM not set")

    # если не передали получателя — отправляем владельцу
    if not to:
        to = owner_email

    payload = {
        "from": from_email,
        "to": [to],
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
        raise Exception(f"Resend error: {response.text}")

    print("EMAIL SENT ✅")