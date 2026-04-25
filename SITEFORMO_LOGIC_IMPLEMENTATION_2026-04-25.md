# SiteFormo logic implementation — 2026-04-25

Implemented from the requested project logic:

- Added `project logic.md` with the full business and technical flow.
- Changed demo public lifetime to 10 minutes.
- Changed internal demo retention to 96 hours.
- Updated `.env.example`, `.env.local.example`, and `.env.railway.example` accordingly.
- Added the correct Twilio-compatible webhook alias: `POST /twilio/webhook`.
- Kept legacy WhatsApp aliases: `/whatsapp/webhook` and `/channels/whatsapp/webhook`.
- Removed the older duplicate Russian echo webhook that shadowed the real WhatsApp router.
- Rebuilt guided web chat as an English-only short order quiz:
  - contact method selection,
  - generated confirmation message for WhatsApp/Telegram/Messenger,
  - project topic / existing website field,
  - up to 3 reference websites,
  - short AI-friendly questionnaire,
  - estimate and 50% deposit message.
- Updated the embeddable widget to support multi-field quiz screens and mobile-first layout.
- Added `/api/orders` routes:
  - `POST /api/orders/intake`,
  - `GET /api/orders/{order_id}`,
  - `POST /api/orders/{order_id}/payment-reported`,
  - `GET /api/orders/{order_id}/decision?action=approve|reject&token=...`,
  - `POST /api/orders/{order_id}/decision/manual?action=approve|reject`,
  - `POST /api/orders/{order_id}/extended-brief`.
- Added owner email sending helpers and restored the missing `send_demo_email` function.
- Added two pre-payment Divi-ready homepage concepts for each order.
- Added final owner delivery email with client contact, topic/source, project type, extended brief, and two Divi-ready samples.

## GitHub commands

From the project root:

```bash
git status
git add .
git commit -m "Implement SiteFormo sales platform project logic"
git push origin main
```

If your branch is not `main`, use:

```bash
git branch --show-current
git push origin $(git branch --show-current)
```

## Railway/Twilio production checks

Twilio Sandbox webhook URL:

```text
https://siteformo-ai-platform-production.up.railway.app/twilio/webhook
```

Method: `POST`

Railway logs should show:

```text
POST /twilio/webhook
```

For a minimal webhook isolation test, temporarily return a static TwiML response from `backend/app/channels/whatsapp.py`. If static TwiML reaches WhatsApp, the route is correct and any remaining issue is inside AI generation.
