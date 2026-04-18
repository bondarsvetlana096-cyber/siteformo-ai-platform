from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone
from urllib.parse import quote

from app.core.config import settings
from app.core.security import sign_asset
from app.services.storage import get_storage


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def publish_demo(request_id: str, html: str) -> tuple[str, str, datetime, datetime, str]:
    now = _utcnow()

    demo_ttl_hours = getattr(settings, "demo_ttl_hours", 24)
    retention_days = getattr(settings, "demo_retention_days", 30)

    expires_at = now + timedelta(hours=demo_ttl_hours)
    retention_expires_at = now + timedelta(days=retention_days)

    token = secrets.token_urlsafe(16)
    master_storage_key = f"demos/{request_id}/master/index.html"

    get_storage().put_text(
        master_storage_key,
        html,
        content_type="text/html; charset=utf-8",
    )

    public_base_url = str(settings.public_base_url).rstrip("/")
    demo_url = f"{public_base_url}/demo/{token}"

    return token, master_storage_key, expires_at, retention_expires_at, demo_url


def build_demo_delivery_html(request_id: str, demo_token: str, html: str) -> str:
    base_url = str(settings.public_base_url).rstrip("/")

    def asset_url(storage_key: str) -> str:
        token = sign_asset(storage_key)
        return f"{base_url}/demo-assets/{quote(token, safe='')}"

    rewritten_html = html

    rewritten_html = rewritten_html.replace('src="/', f'src="{base_url}/')
    rewritten_html = rewritten_html.replace("src='/", f"src='{base_url}/")
    rewritten_html = rewritten_html.replace('href="/', f'href="{base_url}/')
    rewritten_html = rewritten_html.replace("href='/", f"href='{base_url}/")

    delivery_html = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width,initial-scale=1" />
  <meta name="robots" content="noindex,nofollow,noarchive,nosnippet,noimageindex" />
  <title>Demo Preview</title>
  <style>
    html, body {{
      margin: 0;
      padding: 0;
      background: #ffffff;
      width: 100%;
      min-height: 100%;
    }}
    iframe {{
      border: 0;
      display: block;
      width: 100%;
      height: 100vh;
    }}
  </style>
</head>
<body>
{rewritten_html}
</body>
</html>"""

    return delivery_html