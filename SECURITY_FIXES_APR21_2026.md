# Security / Turnstile / Watermark fixes

Applied updates:
- fixed demo-page CSP so Cloudflare Turnstile scripts, frames, and network calls are allowed after demo load
- wired HTML post-processing into publish flow so watermark/protection logic is always applied to stored demo HTML
- strengthened visible watermarks on protected demos
- added a deliberate pre-access gate requiring checkbox + exact phrase + 4-second press-and-hold unlock
- kept existing anti-copy / anti-save / anti-inspect friction in place

Main files changed:
- backend/app/api/routes.py
- backend/app/services/publisher.py
- backend/app/services/html_postprocess.py
