# SiteFormo — Project Status, Logic, and Completed Work

## What was completed

### Infrastructure
- FastAPI backend is prepared for Railway deployment.
- Public webhook endpoint is available at `/channels/telegram/webhook`.
- Start command is set for Railway:
  ```bash
  uvicorn app.main:app --host 0.0.0.0 --port $PORT
  ```

### Git and repository hygiene
- Secrets were removed from the shipped archive.
- `.env.example` was added.
- `.gitignore` excludes `.env`, caches, and local virtual environments.
- `.git` was excluded from the clean archive.

### Telegram architecture fix
- `backend/app/main.py` now imports the correct routers:
  - `app.api.channel_routes`
  - `app.api.routes`
- This fixes the previous issue where the app used the wrong legacy Telegram router.

### Database startup fix
- SQLAlchemy metadata initialization is executed on startup:
  ```python
  Base.metadata.create_all(bind=engine)
  ```
- This ensures core tables are available for local boot and Railway boot.

### Legacy code cleanup
- The old Telegram webhook logic was removed from `app/services/telegram_service.py`.
- `TelegramService` now only handles outbound message sending.

### Bot flow fix
- Bot prompts were rewritten to English.
- Final confirmation is now generated in English only.
- Russian fallback strings were removed from the active chatbot flow.

### AI generation improvement
- `GenerationService` now supports two modes:
  1. Real OpenAI-based concept generation when `OPENAI_API_KEY` is configured.
  2. Safe fallback concept generation when OpenAI is not configured.
- The service generates two different homepage concepts.

---

## Current backend logic

```text
Telegram / WhatsApp / Web chat
→ channel_routes
→ ChatbotService
→ IntakePayload
→ IntakeService
→ PricingService
→ GenerationService
→ concepts saved to DB
→ confirmation sent to user
```

---

## Main files changed
- `backend/app/main.py`
- `backend/app/services/chatbot_service.py`
- `backend/app/services/generation_service.py`
- `backend/app/services/telegram_service.py`
- `backend/.env.example`
- `README.md`
- `docs/PROJECT_STATUS_AND_IMPLEMENTATION_LOG.md`

---

## Current production-safe notes
- Do not commit `.env`.
- Rotate all tokens that were previously exposed.
- Use Railway environment variables instead of local secrets in Git.
- Prefer PostgreSQL/Supabase for production instead of SQLite.

---

## Recommended database architecture

### Core tables already present
- `client_profiles`
- `orders`
- `homepage_concepts`
- `final_packages`
- `conversation_sessions`
- `conversation_messages`

### Recommended next extension
Add explicit operational fields for:
- lead source tracking
- owner review state
- export status
- OpenAI generation metadata
- retry/error logging

---

## Recommended next steps
1. Connect production Postgres on Railway or Supabase.
2. Save full intake payloads and generation metadata.
3. Add owner notifications by email.
4. Add concept selection flow.
5. Add Divi export pipeline.
6. Add structured tests for Telegram and web-chat flows.

---

## Railway environment template
Use values from `backend/.env.example` and set them in Railway:
- `PUBLIC_BASE_URL`
- `OWNER_EMAIL`
- `TELEGRAM_BOT_TOKEN`
- `OPENAI_API_KEY`
- `DATABASE_URL` (optional or production Postgres)
- `APPROVAL_SECRET`

---

## Result
The project is now cleaner, safer, and closer to a deployable MVP:
- correct router connection
- English chatbot flow
- safer secret handling
- AI-ready concept generation
- documentation added in markdown
