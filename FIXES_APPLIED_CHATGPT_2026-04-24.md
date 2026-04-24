# Fixes applied by ChatGPT — 2026-04-24

## Scope
Reviewed project documentation, backend Python code, dependency file, route wiring, and env templates.

## Changes

### 1. Unified backend settings
Updated `backend/app/core/config.py` so every lower-case `settings.*` attribute used by the backend exists with a safe default.

Added/normalized settings for:
- app/runtime and logging
- admin/security/rate limits
- OpenAI/AI limits
- Telegram, WhatsApp, Messenger contacts
- SMTP/email
- demo TTL/retention/follow-up timing
- queue backend and worker polling
- local/Supabase/S3 storage
- Sentry/PostHog telemetry

Also kept uppercase compatibility properties such as `settings.OPENAI_API_KEY`, `settings.ADMIN_API_KEY`, etc., because some older modules still use `app.core.settings`-style names.

### 2. Synchronized env templates
Rebuilt these files so they include all environment variables referenced by the project:
- `backend/.env.example`
- `backend/.env.local.example`
- `backend/.env.railway.example`

No real secrets were added. All secret values remain placeholders.

### 3. Fixed route conflicts
`backend/app/api/channel_routes.py` previously duplicated Telegram/WhatsApp routes already defined in `backend/app/channels/telegram.py` and `backend/app/channels/whatsapp.py`. In FastAPI, the first registered duplicate route can shadow the intended production route.

The file now only exposes `/channels/web-chat/message`, and that endpoint uses the same AI reply service as the Telegram/WhatsApp channel handlers.

### 4. Improved Telegram config loading
`backend/app/channels/telegram.py` no longer snapshots `TELEGRAM_BOT_TOKEN` at import time via `os.getenv`. It now reads from centralized settings.

### 5. Completed dependencies
Updated `backend/requirements.txt` to include libraries imported by the code but missing from the requirements file:
- `beautifulsoup4`
- `boto3`
- `botocore`
- `itsdangerous`
- `posthog`
- `sentry-sdk`
- `supabase`

Also pinned previously unpinned `redis` and `psycopg2-binary`.

## Validation performed
- Parsed all Python files under `backend/app`, `backend/tests`, and `backend/alembic` with `ast.parse`.
- Result: `syntax_errors 0`.
- Compared `settings.*` usage against `backend/app/core/config.py` definitions.
- Result: no missing centralized settings attributes found.
- Compared raw `os.getenv(...)` usages against `backend/.env.example`.
- Result: no missing raw env variables found.

## Notes
Full runtime tests were not executed because the current execution container does not have the project dependencies installed. The changes are static/syntax/configuration fixes and avoid adding real credentials.
