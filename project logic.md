# Demo Website Generator – Technical Specification

## Overview

This project is a web-based system for generating high-quality demo landing pages in under 60 seconds.

The goal is to create a “Wow-effect” demo page that convinces users to purchase a full website.

---

## Core Stack

- Frontend / Main Website: Hostinger
- Backend API: FastAPI (Railway or Vercel)
- Database & Storage: Supabase
- AI Generation: OpenAI
- Email Service: Resend
- Automation & Scraping: Playwright
- Bot Protection: Cloudflare Turnstile
- Queues & Jobs: Supabase Queues / pg_cron
- Analytics: PostHog
- Error Tracking: Sentry

---

## User Flow

### Step 1: Entry Point
User arrives via:
- Ads
- Email campaigns
- Direct link

Clicks button:
“Preview your new or future website in 60 seconds”

---

### Step 2: Input Form

User must fill both fields:

1. Field 1:
   - Website topic OR existing website URL

2. Field 2:
   - Email or Telegram

Rules:
- If one field is empty → generation button is disabled
- Turnstile validation required

---

### Step 3: Processing Logic

Case A — Email:
- Demo generated immediately

Case B — Telegram:
- User must initiate chat first
- Then demo is generated

---

## Demo Generation Rules

- Max 2 generations per user
- 3rd attempt → redirect to order page

---

## Demo Page Behavior

Must include:
- Warning about 2 attempts
- CTA button “Order your website”
- High-quality design

---

## Demo Lifetime

- Public: 10 minutes
- Internal storage: 96 hours

---

## Security & Anti-Abuse

- Input normalization
- Rate limiting
- Turnstile validation
- Duplicate detection

---

## Special Exception

klon97048@gmail.com bypasses all limits

---

## API Endpoints

POST /api/demo/start  
POST /api/demo/generate  
GET /demo/{token}  
POST /api/order/started  
POST /api/order/paid  

---

## Final Goal

Create a system where user sees WOW demo and converts to purchase.
