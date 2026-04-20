# Fixes applied

## Files changed
- `backend/app/core/config.py`
- `backend/app/services/queue.py`
- `backend/app/services/publisher.py`
- `backend/app/services/turnstile.py`

## What was fixed
1. Restored a backward-compatible `Settings` model that matches the existing project and `.env` keys.
2. Added `database_url` and all other settings used across API, worker, storage, telemetry, rate limiting, and follow-ups.
3. Added compatibility aliases for env keys like `DEMO_BASE_URL -> public_base_url`, `SECRET_KEY -> asset_signing_secret`, etc.
4. Normalized `APP_ENV` values such as `production` and `development` so the app no longer fails on startup.
5. Fixed DB queue fallback in `enqueue_job(...)` so non-Supabase queue mode works again.
6. Fixed demo publish TTL/retention logic to use `DEMO_TTL_MINUTES` and `DEMO_RETENTION_HOURS` consistently.
7. Fixed Turnstile development check to treat `local`, `dev`, and `development` as development environments.

## Verification performed
- `python -m py_compile $(find app -name '*.py')`
- Import check for `app.main`, `app.workers.worker`, `app.services.request_service`, `app.services.queue`, `app.services.publisher`
- Smoke test for `publish_demo(...)` with local storage
