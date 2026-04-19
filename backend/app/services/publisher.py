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

    expires_at = now + timedelta(minutes=settings.demo_ttl_minutes)
    retention_expires_at = now + timedelta(hours=settings.demo_retention_hours)

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


def build_demo_delivery_html(
    request_id: str,
    demo_token: str,
    html: str,
    continue_url: str,
    free_limit_text: str = "You can generate 2 free demos before placing an order.",
) -> str:
    base_url = str(settings.public_base_url).rstrip("/")

    def asset_url(storage_key: str) -> str:
        token = sign_asset(storage_key)
        return f"{base_url}/demo-assets/{quote(token, safe='')}"

    rewritten_html = html
    rewritten_html = rewritten_html.replace('src="/', f'src="{base_url}/')
    rewritten_html = rewritten_html.replace("src='/", f"src='{base_url}/")
    rewritten_html = rewritten_html.replace('href="/', f'href="{base_url}/')
    rewritten_html = rewritten_html.replace("href='/", f"href='{base_url}/")

    overlay_block = f"""
<div id="siteformo-demo-shell">
  <div id="siteformo-demo-topnote">
    <div class="sf-note-badge">
      <span class="sf-note-dot"></span>
      <span>{free_limit_text}</span>
    </div>
  </div>

  <div id="siteformo-demo-cta">
    <div class="sf-cta-card">
      <div class="sf-cta-text">
        <div class="sf-cta-label">Siteformo</div>
        <div class="sf-cta-title">Ready to order your website?</div>
        <div class="sf-cta-subtitle">Continue on the main Siteformo website.</div>
      </div>
      <a
        href="{continue_url}"
        onclick="window.siteformoTrackCta&&window.siteformoTrackCta()"
        class="sf-cta-button"
      >
        Order on Siteformo
      </a>
    </div>
  </div>
</div>

<script>
(function() {{
  var endpoint = '/api/requests/{request_id}/events';

  window.siteformoTrackCta = function() {{
    var payload = JSON.stringify({{
      event_type: 'demo_cta_clicked',
      metadata: {{
        source: 'demo_overlay',
        demo_token: '{demo_token}',
        destination: '{continue_url}'
      }}
    }});

    if (navigator.sendBeacon) {{
      navigator.sendBeacon(endpoint, new Blob([payload], {{ type: 'application/json' }}));
      return true;
    }}

    fetch(endpoint, {{
      method: 'POST',
      headers: {{ 'Content-Type': 'application/json' }},
      body: payload,
      keepalive: true
    }}).catch(function(){{}});

    return true;
  }};
}})();
</script>
"""

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
      width: 100%;
      min-height: 100%;
      background: #ffffff;
    }}

    #siteformo-demo-topnote {{
      position: fixed;
      top: 14px;
      left: 14px;
      z-index: 2147483646;
      max-width: min(560px, calc(100vw - 28px));
    }}

    .sf-note-badge {{
      display: inline-flex;
      align-items: center;
      gap: 10px;
      padding: 10px 14px;
      border-radius: 999px;
      background: rgba(15, 23, 42, 0.82);
      color: #ffffff;
      border: 1px solid rgba(255,255,255,0.16);
      box-shadow: 0 10px 28px rgba(0,0,0,0.22);
      backdrop-filter: blur(10px);
      font: 600 13px/1.35 Inter, Arial, sans-serif;
    }}

    .sf-note-dot {{
      width: 8px;
      height: 8px;
      border-radius: 999px;
      background: #22c55e;
      flex: 0 0 auto;
    }}

    #siteformo-demo-cta {{
      position: fixed;
      right: 18px;
      bottom: 18px;
      z-index: 2147483647;
      width: min(420px, calc(100vw - 24px));
    }}

    .sf-cta-card {{
      display: flex;
      flex-direction: column;
      gap: 14px;
      padding: 16px;
      border-radius: 18px;
      background: rgba(15, 23, 42, 0.88);
      color: #ffffff;
      border: 1px solid rgba(255,255,255,0.12);
      box-shadow: 0 18px 50px rgba(0,0,0,0.28);
      backdrop-filter: blur(12px);
      font-family: Inter, Arial, sans-serif;
    }}

    .sf-cta-label {{
      font-size: 12px;
      font-weight: 700;
      text-transform: uppercase;
      letter-spacing: .08em;
      color: rgba(255,255,255,.72);
      margin-bottom: 4px;
    }}

    .sf-cta-title {{
      font-size: 18px;
      font-weight: 800;
      line-height: 1.2;
      color: #ffffff;
    }}

    .sf-cta-subtitle {{
      margin-top: 4px;
      font-size: 14px;
      line-height: 1.45;
      color: rgba(255,255,255,.78);
    }}

    .sf-cta-button {{
      display: inline-flex;
      align-items: center;
      justify-content: center;
      min-height: 48px;
      padding: 0 18px;
      border-radius: 14px;
      text-decoration: none;
      font: 800 14px/1 Inter, Arial, sans-serif;
      color: #ffffff;
      background: linear-gradient(90deg, #7c3aed, #06b6d4);
      box-shadow: 0 12px 30px rgba(12, 74, 110, 0.3);
    }}

    @media (max-width: 640px) {{
      #siteformo-demo-topnote {{
        top: 10px;
        left: 10px;
        right: 10px;
        max-width: none;
      }}

      #siteformo-demo-cta {{
        left: 10px;
        right: 10px;
        bottom: 10px;
        width: auto;
      }}

      .sf-cta-card {{
        padding: 14px;
        border-radius: 16px;
      }}

      .sf-cta-title {{
        font-size: 16px;
      }}

      .sf-cta-subtitle {{
        font-size: 13px;
      }}
    }}
  </style>
</head>
<body>
{rewritten_html}
{overlay_block}
</body>
</html>"""

    return delivery_html
