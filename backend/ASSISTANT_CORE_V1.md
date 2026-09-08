# SiteFormo Assistant Core V1

This is infrastructure only: no persona, funnel or stage policy, tools, actions, payments, generation, editing, market logic, or production frontend widget.

## Isolated configuration

The Assistant reads only `SITEFORMO_ASSISTANT_ENABLED`, `SITEFORMO_ASSISTANT_OPENAI_API_KEY`, `SITEFORMO_ASSISTANT_OPENAI_MODEL`, `SITEFORMO_ASSISTANT_OPENAI_TIMEOUT_SECONDS`, and `SITEFORMO_ASSISTANT_OPENAI_MAX_RETRIES`. Legacy `OPENAI_*` values are not aliases or fallbacks. Disabled routes return 404 before constructing an Assistant OpenAI client or doing Assistant business work.

## Identity and persistence

The server issues a 256-bit opaque possession credential in a host-only, Secure, HttpOnly, SameSite=Lax cookie with `Path=/` and `Max-Age=31536000`. The `Domain` attribute is omitted. Only its SHA-256 hash is stored. IP addresses, fingerprints, and request IDs do not establish ownership.

PostgreSQL is canonical storage in `assistant_visitors`, `assistant_conversations`, and `assistant_messages`. Assistant models have separate metadata and are never part of startup `create_all()`.

Production is not currently Alembic-managed and must not be stamped with an invented history. Revision `0007_assistant_core_v1`, following `0006_design_screenshot_flow`, remains the repository and test schema specification. Production installation uses the explicit one-off TARGET-native command instead:

```text
python -m app.services.db.assistant_schema_installer verify
python -m app.services.db.assistant_schema_installer install
python -m app.services.db.assistant_schema_installer verify
```

`verify` is read-only and reports `ABSENT`, `EXACT`, `PARTIAL`, or `MISMATCH`. `install` takes a PostgreSQL transactional advisory lock, installs the canonical `AssistantBase` metadata only from `ABSENT`, verifies it before commit, and is a no-op from `EXACT`. `PARTIAL` and `MISMATCH` are hard-stop states and are never repaired automatically. The installer is not imported by API startup, worker startup, `init_db()`, or the lightweight migrations. No Alembic stamp is used. Keep `SITEFORMO_ASSISTANT_ENABLED=false` throughout schema installation and verification.

## API and transport

- `POST /api/assistant/session` creates or resumes a cookie-owned conversation and returns bounded history.
- `GET /api/assistant/history` requires the possession cookie and verifies conversation ownership.
- `POST /api/assistant/message` requires `client_message_id` and streams `start`, `delta`, `done`, or sanitized `error` SSE events.

State-changing Assistant requests require the exact Origin `https://ie.siteformo.com`. A conversation ID alone grants no access. User turns are committed once before inference; an Assistant turn is committed only after a complete successful stream. Recent model context is bounded to 40 completed messages.

Production browser traffic must use the relative `https://ie.siteformo.com/api/assistant/*` path through an external same-origin reverse proxy to TARGET FastAPI. The proxy must preserve SSE streaming and the original Origin, disable buffering and caching, forward cookies and `Set-Cookie`, and forward correct host/protocol information. Direct browser-to-Railway Assistant calls are unsupported. The production frontend and proxy are outside this repository.

## Deployment warning

The observed Railway Watch Path is `backend/src/**`, while runtime and migration changes live under `backend/app/**`, `backend/alembic/**`, and tests under `backend/tests/**`. This must be corrected before relying on automatic deployment. Railway configuration is intentionally unchanged here.

## Known limits

The rate limiter is process-local and only a first layer. There is no production widget, distributed limiter, retention/deletion workflow, moderation policy, persona, journey policy, customer/project binding, or action capability in V1. PostgreSQL migration and proxy/cookie behavior must be verified in isolated/staging environments before production use.
