from __future__ import annotations

import re
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

    # Optional signed asset rewrite support if later you emit internal storage keys.
    rewritten_html = re.sub(
        r'src="(demos/[^"]+|masters/[^"]+)"',
        lambda m: f'src="{asset_url(m.group(1))}"',
        rewritten_html,
        flags=re.I,
    )
    rewritten_html = re.sub(
        r"src='(demos/[^']+|masters/[^']+)'",
        lambda m: f"src='{asset_url(m.group(1))}'",
        rewritten_html,
        flags=re.I,
    )

    overlay_block = f"""
<div id="siteformo-demo-topnote" aria-live="polite">
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
      target="_blank"
      rel="noopener noreferrer"
      onclick="return window.siteformoTrackCta ? window.siteformoTrackCta(event) : true;"
      class="sf-cta-button"
    >
      Order on Siteformo
    </a>
  </div>
</div>

<script>
(function() {{
  var endpoint = '/api/requests/{request_id}/events';

  window.siteformoTrackCta = function(event) {{
    var destination = {continue_url!r};
    var payload = JSON.stringify({{
      event_type: 'demo_cta_clicked',
      metadata: {{
        source: 'demo_overlay',
        demo_token: '{demo_token}',
        destination: destination
      }}
    }});

    try {{
      if (navigator.sendBeacon) {{
        navigator.sendBeacon(endpoint, new Blob([payload], {{ type: 'application/json' }}));
      }} else {{
        fetch(endpoint, {{
          method: 'POST',
          headers: {{ 'Content-Type': 'application/json' }},
          body: payload,
          keepalive: true
        }}).catch(function(){{}});
      }}
    }} catch (e) {{}}

    return true;
  }};
}})();
</script>
"""

    overlay_styles = """
<style id="siteformo-demo-overlay-styles">
  #siteformo-demo-topnote {
    position: fixed !important;
    top: 14px !important;
    left: 14px !important;
    z-index: 2147483646 !important;
    max-width: min(560px, calc(100vw - 28px)) !important;
    pointer-events: none !important;
  }

  .sf-note-badge {
    display: inline-flex !important;
    align-items: center !important;
    gap: 10px !important;
    padding: 10px 14px !important;
    border-radius: 999px !important;
    background: rgba(15, 23, 42, 0.88) !important;
    color: #ffffff !important;
    border: 1px solid rgba(255,255,255,0.16) !important;
    box-shadow: 0 10px 28px rgba(0,0,0,0.22) !important;
    backdrop-filter: blur(10px) !important;
    font: 600 13px/1.35 Inter, Arial, sans-serif !important;
  }

  .sf-note-dot {
    width: 8px !important;
    height: 8px !important;
    border-radius: 999px !important;
    background: #22c55e !important;
    flex: 0 0 auto !important;
  }

  #siteformo-demo-cta {
    position: fixed !important;
    right: 18px !important;
    bottom: 18px !important;
    z-index: 2147483647 !important;
    width: min(420px, calc(100vw - 24px)) !important;
  }

  .sf-cta-card {
    display: flex !important;
    flex-direction: column !important;
    gap: 14px !important;
    padding: 16px !important;
    border-radius: 18px !important;
    background: rgba(15, 23, 42, 0.92) !important;
    color: #ffffff !important;
    border: 1px solid rgba(255,255,255,0.12) !important;
    box-shadow: 0 18px 50px rgba(0,0,0,0.28) !important;
    backdrop-filter: blur(12px) !important;
    font-family: Inter, Arial, sans-serif !important;
  }

  .sf-cta-label {
    font-size: 12px !important;
    font-weight: 700 !important;
    text-transform: uppercase !important;
    letter-spacing: .08em !important;
    color: rgba(255,255,255,.72) !important;
    margin-bottom: 4px !important;
  }

  .sf-cta-title {
    font-size: 18px !important;
    font-weight: 800 !important;
    line-height: 1.2 !important;
    color: #ffffff !important;
  }

  .sf-cta-subtitle {
    margin-top: 4px !important;
    font-size: 14px !important;
    line-height: 1.45 !important;
    color: rgba(255,255,255,.78) !important;
  }

  .sf-cta-button {
    display: inline-flex !important;
    align-items: center !important;
    justify-content: center !important;
    min-height: 48px !important;
    padding: 0 18px !important;
    border-radius: 14px !important;
    text-decoration: none !important;
    font: 800 14px/1 Inter, Arial, sans-serif !important;
    color: #ffffff !important;
    background: linear-gradient(90deg, #7c3aed, #06b6d4) !important;
    box-shadow: 0 12px 30px rgba(12, 74, 110, 0.30) !important;
    border: 0 !important;
    cursor: pointer !important;
  }

  @media (max-width: 640px) {
    #siteformo-demo-topnote {
      top: 10px !important;
      left: 10px !important;
      right: 10px !important;
      max-width: none !important;
    }

    #siteformo-demo-cta {
      left: 10px !important;
      right: 10px !important;
      bottom: 10px !important;
      width: auto !important;
    }

    .sf-cta-card {
      padding: 14px !important;
      border-radius: 16px !important;
    }

    .sf-cta-title {
      font-size: 16px !important;
    }

    .sf-cta-subtitle {
      font-size: 13px !important;
    }
  }
</style>
"""

    if re.search(r"</head\s*>", rewritten_html, flags=re.I):
        rewritten_html = re.sub(
            r"</head\s*>",
            overlay_styles + "\n</head>",
            rewritten_html,
            count=1,
            flags=re.I,
        )
    else:
        rewritten_html = overlay_styles + rewritten_html

    if re.search(r"</body\s*>", rewritten_html, flags=re.I):
        rewritten_html = re.sub(
            r"</body\s*>",
            overlay_block + "\n</body>",
            rewritten_html,
            count=1,
            flags=re.I,
        )
        return rewritten_html

    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width,initial-scale=1" />
  <meta name="robots" content="noindex,nofollow,noarchive,nosnippet,noimageindex" />
  <title>Demo Preview</title>
  {overlay_styles}
</head>
<body>
{rewritten_html}
{overlay_block}
</body>
</html>"""