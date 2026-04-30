# SiteFormo v5 patch — questionnaire -> WOW previews flow

This package was updated for the correct SiteFormo production flow:

Payment -> extended questionnaire -> 5 design previews (+ logos if ordered) -> client selects design -> refund window -> full generation -> final payment.

## What changed

### Frontend / WPCode

New file:

- `frontend/extended_questionnaire_v5_WPCode.html`

This is the new simplified AI-ready questionnaire.

It includes:

- required contact details
- company name and location
- business type and goal
- package-based page presets
  - Starter: 3 pages
  - Business: 4 pages
  - Premium: 5 pages
- simple page type + page goal choices
- Design quality level
  - Standard
  - High-end
  - WOW design (recommended, default)
- optional free form protection
  - no extra protection
  - invisible spam protection, free
- references optional
  - if references are provided, they are improved automatically
  - if no references are provided, the backend prompt engine creates a best-in-class design
- no open free-text project notes

### Backend

New file:

- `backend/app/services/prompt_service.py`

This is the SiteFormo AI Prompt Engine. It normalizes questionnaire answers and adds the Enhancement Layer so OpenAI receives a clean, premium design prompt.

Updated file:

- `backend/app/services/generation_service.py`

Preview generation now uses the prompt engine and creates 5 distinct enhanced prompts:

- Design A: recommended premium direction
- Design B: luxury high-end direction
- Design C: bold conversion direction
- Design D: warm trust direction
- Design E: minimal corporate direction

Updated file:

- `backend/app/api/order_routes.py`

`POST /api/orders/extended-brief` remains the correct endpoint. It saves the questionnaire, sets `BRIEF_SUBMITTED`, generates previews, sets `DESIGN_PREVIEWS_READY`, and emails the client the preview link.

Updated file:

- `backend/app/api/stripe_webhook.py`

The old legacy `/extended-brief` route that generated the full site immediately has been disabled/renamed. The correct endpoint is `/api/orders/extended-brief`.

Updated file:

- `backend/app/main.py`

Duplicate CORS middleware was cleaned up.

## Critical product rule

Do not generate the full website after payment.

The correct trigger for full generation is client design selection, not Stripe payment and not questionnaire submission.

Questionnaire submission should generate previews only.
