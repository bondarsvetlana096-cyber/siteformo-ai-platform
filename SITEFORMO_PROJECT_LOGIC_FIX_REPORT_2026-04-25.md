# SiteFormo project-logic implementation report — 2026-04-25

Implemented against `project logic.md`.

## Key fixes

- Added public demo request API in `backend/app/api/request_routes.py`:
  - `POST /api/requests`
  - `GET /api/requests/{request_id}`
  - `POST /api/requests/{request_id}/confirm`
  - `POST /api/requests/{request_id}/events`
  - `GET /demo/{demo_token}`
  - `GET /demo-assets/{asset_token}`
- Connected the new request routes in `backend/app/main.py`.
- Fixed request/demo database initialization:
  - `Request`, `UserUsage`, `Job`, `DemoAsset`, and `EventLog` now use the same SQLAlchemy `Base` as the app.
  - `init_db()` imports request models before `create_all()`.
- Enforced project demo limits:
  - free demo limit set to 2 attempts by default;
  - public demo TTL remains 10 minutes;
  - internal retention remains 96 hours.
- Implemented channel confirmation behavior for WhatsApp, Telegram, and Messenger:
  - non-email demo requests start in `created` state;
  - a unique confirmation message is generated;
  - generation is queued only after confirmation.
- Kept the Twilio route `/twilio/webhook` and the backward-compatible WhatsApp aliases.
- Fixed order/final package model relationship bugs:
  - `Order.final_packages` is now the canonical relationship;
  - legacy `Order.packages` remains as a compatibility property.
- Fixed owner delivery/admin bugs caused by non-existent `order.email` references.
- Fixed `DeliveryService` so it uses the current config fields: `approval_secret` and `public_base_url`.
- Kept the owner bypass logic for `klon97048@gmail.com` through `PAYMENT_APPROVAL_BYPASS_EMAILS` / `OWNER_EMAIL`.
- Updated public-facing pricing explanations and AI fallback messages to English.
- Updated `backend/app/static/web-chat-demo.html` to English.

## Validation performed

- Syntax compilation passed for the whole backend app:

```bash
cd backend
env -i PATH=/usr/bin:/bin python3 -m compileall -q app
```

`pytest` was not available in the system Python inside this container, so a full pytest run could not be executed here.

## Important environment variables

Recommended Railway variables:

```bash
RESEND_API_KEY=...
EMAIL_FROM="SiteFormo <hello@siteformo.com>"
OWNER_EMAIL=klon97048@gmail.com
APPROVAL_SECRET=change-this-in-production
ADMIN_API_KEY=change-this-in-production
PAYMENT_APPROVAL_BYPASS_EMAILS=klon97048@gmail.com
PUBLIC_BASE_URL=https://your-railway-api-domain
MAIN_SITE_BASE_URL=https://siteformo.com
DEMO_TTL_MINUTES=10
DEMO_RETENTION_HOURS=96
FREE_ATTEMPT_LIMIT=2
WHATSAPP_PROVIDER=twilio
WHATSAPP_PUBLIC_NUMBER=...
TELEGRAM_BOT_USERNAME=...
```
