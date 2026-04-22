# SiteFormo AI Sales Platform

SiteFormo is a FastAPI-based sales intake backend for website leads from Telegram, WhatsApp, and web chat.

## Current status
- FastAPI app runs from `backend/app/main.py`
- Telegram webhook route is active at `/channels/telegram/webhook`
- Correct channel router is connected in `main.py`
- Database tables are created automatically on startup
- Legacy Telegram webhook router has been removed
- `.env` is excluded and `.env.example` is included
- Bot flow is now English-first
- AI concept generation supports a real OpenAI call when `OPENAI_API_KEY` is configured

## Run locally
```bash
cd backend
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload
```

## Railway start command
```bash
uvicorn app.main:app --host 0.0.0.0 --port $PORT
```

## Required environment variables
See `backend/.env.example`.

Main values:
- `TELEGRAM_BOT_TOKEN`
- `OWNER_EMAIL`
- `OPENAI_API_KEY`
- `DATABASE_URL` (optional; SQLite is used by default)

## Main flow
```text
User
→ Telegram / WhatsApp / Web chat
→ FastAPI webhook
→ ChatbotService
→ IntakeService
→ GenerationService
→ Pricing / status
→ database
```

## Next recommended step
Move from SQLite/local persistence to PostgreSQL or Supabase for production and store all sessions, requests, and outputs persistently.
