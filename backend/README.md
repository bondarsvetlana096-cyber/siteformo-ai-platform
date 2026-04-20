# Siteformo backend

Backend for the updated Siteformo funnel:
- FastAPI API
- PostgreSQL + Alembic
- OpenAI demo-page generation
- Supabase Storage for demo pages
- Supabase queues or DB fallback for jobs
- Cloudflare Turnstile validation
- Resend email follow-ups
- event-based funnel tracking

## Updated flow
1. User opens the main site.
2. User submits contact details plus a homepage URL or business topic.
3. Backend creates a request and queues demo generation immediately for every supported contact type.
4. Worker generates a master HTML version, stores it for 96 hours, then issues a short-lived `/demo/{token}` public window.
5. Demo page is rendered from the stored master version through a protected delivery wrapper and shows a built-in CTA that sends the user back to the main site.
6. Main site tracks the funnel through request events:
   - `demo_opened`
   - `demo_cta_clicked`
   - `main_form_started`
   - `main_form_completed`
   - `payment_started`
   - `payment_completed`
7. If the user leaves without finishing, a delayed follow-up job sends an email reminder or prepares an outbound message for Telegram.

## Important changes
- No mandatory pre-confirmation step for Telegram before generation.
- Additional messaging channels are intentionally disabled until their integrations are implemented.
- No automatic "demo ready" email at generation time.
- Reminder logic now depends on unfinished actions on the main site.
- API and worker must use the same storage backend.
- Public demo access lasts 10 minutes, while the stored master page remains available internally for 96 hours.
- Expiring public demo access no longer deletes the stored master page.

## Main API
### Create request
`POST /api/requests`

Compatible payloads:
```json
{
  "contact_type": "email",
  "contact_value": "client@example.com",
  "source_input": "https://example.com",
  "turnstile_token": "..."
}
```

Or explicit create mode:
```json
{
  "request_type": "create",
  "contact_type": "telegram",
  "contact_value": "@client_handle",
  "business_description": "landing page for a car service in Dublin",
  "turnstile_token": "..."
}
```

### Track funnel event
`POST /api/requests/{request_id}/events`

```json
{
  "event_type": "payment_started",
  "metadata": {"source": "checkout_page"}
}
```

### Request status
`GET /api/requests/{request_id}`

### Demo page
`GET /demo/{token}`

This endpoint now loads the stored master HTML and wraps it at request time into a protected delivery page.

## Required env
Use the same values in both API and worker:
- `STORAGE_BACKEND=supabase`
- `SUPABASE_URL=...`
- `SUPABASE_SERVICE_ROLE_KEY=...`
- `SUPABASE_STORAGE_BUCKET=siteformo-demo`
- `SUPABASE_STORAGE_BUCKET_DEMOS=siteformo-demo`
- `SUPABASE_STORAGE_BUCKET_ASSETS=demo-assets`
- `QUEUE_BACKEND=supabase`
- `SUPABASE_QUEUE_NAME=generate_demo_queue`
- `DEMO_BASE_URL=https://siteformo-production.up.railway.app`
- `DEMO_TTL_MINUTES=10`
- `DEMO_RETENTION_HOURS=96`

## Local run
```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.local.example .env.local
playwright install chromium
python - <<'PY'
from app.db.base import Base
from app.db.session import engine
import app.models.request  # noqa: F401
Base.metadata.create_all(bind=engine)
print("local db ready")
PY
uvicorn app.main:app --reload
```

Worker in another terminal:
```bash
python -m app.workers.worker
```

## Railway
### Web service
```bash
uvicorn app.main:app --host 0.0.0.0 --port $PORT
```

### Worker service
```bash
python -m app.workers.worker
```

## Supabase SQL
Run:
1. `supabase/setup_queues.sql`
2. `supabase/storage_and_cleanup.sql`
3. `alembic upgrade head`
