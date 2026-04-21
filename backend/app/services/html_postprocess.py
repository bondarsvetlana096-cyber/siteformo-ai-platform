from __future__ import annotations

import re

from app.core.config import settings
from app.core.security import sign_asset


PATTERN = re.compile(r'(siteformo://asset/|/internal-assets/)([^"\'\s>]+)')


PROTECTION_BLOCK = '''
<meta name="robots" content="noindex,nofollow,noarchive,noimageindex,nosnippet" />
<meta http-equiv="Cache-Control" content="no-store, no-cache, must-revalidate, max-age=0" />
<meta http-equiv="Pragma" content="no-cache" />
<meta http-equiv="Expires" content="0" />
<style>
html{-webkit-user-select:none;user-select:none;-webkit-touch-callout:none}
img,video{pointer-events:none}
iframe[src*='challenges.cloudflare.com'], .cf-turnstile, [name='cf-turnstile-response']{pointer-events:auto !important}
@media print { html,body{display:none !important} }
body::before{
  content:"SITEFORMO DEMO • PRIVATE PREVIEW • ORDER ON SITEFORMO • NO SCREENSHOTS • SITEFORMO DEMO";
  position:fixed;inset:0;z-index:2147483645;pointer-events:none;
  display:flex;align-items:center;justify-content:center;
  font:700 28px/1.2 Inter,Arial,sans-serif;letter-spacing:.18em;
  color:rgba(255,255,255,.085);transform:rotate(-24deg);white-space:nowrap;text-shadow:0 2px 18px rgba(0,0,0,.22);
}
body::after{
  content:"Siteformo Demo • Private Preview";
  position:fixed;right:16px;bottom:12px;z-index:2147483647;
  font:600 12px/1.2 Inter,Arial,sans-serif;color:rgba(255,255,255,.58);
  background:rgba(0,0,0,.28);padding:8px 10px;border:1px solid rgba(255,255,255,.12);
  border-radius:999px;backdrop-filter:blur(8px)
}
</style>
<script>
(function(){
  const kill = (e)=>e.preventDefault();
  document.addEventListener('contextmenu', kill);
  document.addEventListener('dragstart', kill);
  document.addEventListener('copy', kill);
  document.addEventListener('cut', kill);
  document.addEventListener('keydown', function(e){
    const k=(e.key||'').toLowerCase();
    if((e.ctrlKey||e.metaKey)&&['s','u','p','c','x','a'].includes(k)) e.preventDefault();
    if(e.shiftKey&&['i','j','c'].includes(k)) e.preventDefault();
    if(k==='f12'||k==='printscreen') e.preventDefault();
  });
  let lastW=window.outerWidth-window.innerWidth;
  let lastH=window.outerHeight-window.innerHeight;
  setInterval(function(){
    const dw=window.outerWidth-window.innerWidth;
    const dh=window.outerHeight-window.innerHeight;
    if(dw>220||dh>220||Math.abs(dw-lastW)>160||Math.abs(dh-lastH)>160){
      document.body.style.filter='blur(10px)';
    } else {
      document.body.style.filter='';
    }
    lastW=dw; lastH=dh;
  }, 1200);
})();
</script>
'''


def rewrite_asset_urls(html: str) -> str:
    def repl(match: re.Match[str]) -> str:
        key = match.group(2)
        token = sign_asset(key)
        return f'/demo-assets/{token}'
    html = PATTERN.sub(repl, html)
    if settings.demo_protection_enabled and '</head>' in html.lower():
        html = re.sub(r'</head>', PROTECTION_BLOCK + '\n</head>', html, count=1, flags=re.I)
    return html
