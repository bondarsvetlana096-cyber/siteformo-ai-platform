# SiteFormo Backend + Frontend Bundle

Clean production-oriented bundle aligned with the current SiteFormo logic.

## Flow

Questionnaire → Design Direction → Homepage Interaction Style → Production Confirmation → Automatic Production Email → Protected Review → Structured Revisions → Final ZIP Delivery.

## Install

1. Deploy `backend/` to Railway.
2. Copy variables from `backend/.env.example` into Railway Variables.
3. Put frontend WPCode files from `frontend/` into the matching WordPress pages.
4. Do not upload old QA folders, `.git`, `node_modules`, or historical patch markdown files.

## Key API areas

- `/api/orders/...` — order and questionnaire flow.
- `/api/review/...` — protected review and revision flow.
- `/api/stripe/webhook` — Stripe payment production email flow.

## Current business rule

Preview access is not final delivery. ZIP/source files are available only after final approval.
