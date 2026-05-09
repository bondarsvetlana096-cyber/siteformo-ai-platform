# SiteFormo Backend

FastAPI backend for the current SiteFormo production pipeline.

## Current flow

Questionnaire → Design Direction → Homepage Interaction Style → Production Confirmation → Automatic Production Email → Protected Review → Structured Revisions → Final ZIP Delivery.

## Production services kept in this bundle

- FastAPI API service.
- Stripe webhook service.
- Resend email sending through `hello@siteformo.com`.
- Order/design/review API routes.
- Optional worker service for queued generation jobs.
- Optional Telegram/WhatsApp channel files remain in the codebase but are not required for the main SiteFormo order flow.

## Removed from ZIP

- `.git` repository internals.
- `node_modules`.
- QA screenshots and generated reports.
- historical patch `.md` files.
- duplicate old `app/db/api` route copy.

## Railway

Start command:

```bash
uvicorn app.main:app --host 0.0.0.0 --port $PORT
```

Copy variables from `.env.railway.example` into Railway Variables. Do not commit real API keys into Git.
