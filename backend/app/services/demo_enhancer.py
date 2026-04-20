from __future__ import annotations

from app.services.followups import build_main_site_url


def inject_demo_cta(html: str, request_id: str, demo_token: str, continue_url: str) -> str:
    snippet = f"""
<div id=\"siteformo-demo-cta\" style=\"position:fixed;right:20px;bottom:20px;z-index:2147483647;font-family:Inter,Arial,sans-serif\">
  <a href=\"{continue_url}\" onclick=\"window.siteformoTrackCta&&window.siteformoTrackCta()\" style=\"display:inline-flex;align-items:center;gap:10px;padding:14px 18px;border-radius:16px;background:linear-gradient(90deg,#7c3aed,#06b6d4);color:#fff;text-decoration:none;font-weight:800;box-shadow:0 12px 34px rgba(0,0,0,.28)\">Go to main site form</a>
</div>
<script>
(function() {{
  var endpoint = '/api/requests/{request_id}/events';
  window.siteformoTrackCta = function() {{
    var payload = JSON.stringify({{event_type:'demo_cta_clicked', metadata:{{source:'demo_overlay', demo_token:'{demo_token}'}}}});
    if (navigator.sendBeacon) {{
      navigator.sendBeacon(endpoint, new Blob([payload], {{type:'application/json'}}));
      return true;
    }}
    fetch(endpoint, {{method:'POST', headers:{{'Content-Type':'application/json'}}, body:payload, keepalive:true}}).catch(function(){{}});
    return true;
  }};
}})();
</script>
"""
    if "</body>" in html:
        return html.replace("</body>", snippet + "\n</body>")
    return html + snippet
