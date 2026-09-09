# SiteFormo Assistant Core V1

This is infrastructure only: no persona, funnel or stage policy, tools, actions, payments, generation, editing, market logic, or production frontend widget.

## Isolated configuration

The Assistant reads only `SITEFORMO_ASSISTANT_ENABLED`, `SITEFORMO_ASSISTANT_OPENAI_API_KEY`, `SITEFORMO_ASSISTANT_OPENAI_MODEL`, `SITEFORMO_ASSISTANT_OPENAI_TIMEOUT_SECONDS`, and `SITEFORMO_ASSISTANT_OPENAI_MAX_RETRIES`. Legacy `OPENAI_*` values are not aliases or fallbacks. Disabled routes return 404 before constructing an Assistant OpenAI client or doing Assistant business work.

## Journey identity and persistence

SiteFormo Journey Identity V1 is neutral funnel infrastructure. `POST /api/journey/session` issues a 256-bit opaque credential before Assistant use and stores only its SHA-256 hash in `siteformo_visitors`. A valid credential resumes the same visitor. A missing or forged credential at this bootstrap endpoint creates a new anonymous visitor; Assistant endpoints never do so implicitly. IP addresses, fingerprints, request IDs, order IDs, emails, and payment tokens do not establish ownership.

Direct browser-to-Railway clients present the scoped credential in `X-SiteFormo-Visitor`. It authorizes only neutral Journey and Assistant ownership and grants no order, payment, customer, generation, or admin access. The old `sf_assistant_visitor` cookie constants remain compatibility-only; Assistant routes neither issue nor accept that cookie as ownership.

PostgreSQL is canonical storage in `siteformo_visitors`, `assistant_visitors`, `assistant_conversations`, and `assistant_messages`. `assistant_visitors.siteformo_visitor_id` is a required unique cascading foreign key, producing one Assistant binding per SiteFormo visitor. The mirrored `assistant_visitors.credential_hash` preserves the historical Core V1 schema but contains the same Journey credential hash, not a second credential. Journey/Assistant models have separate metadata and are never part of startup `create_all()`.

Production is not currently Alembic-managed and must not be stamped with an invented history. Revision `0007_assistant_core_v1`, following `0006_design_screenshot_flow`, remains the historical Assistant Core specification. Revision `0008_siteformo_journey_identity_v1` is the repository/test specification for the additive Journey evolution. Production installation uses the explicit one-off TARGET-native command instead:

```text
python -m app.services.db.assistant_schema_installer verify
python -m app.services.db.assistant_schema_installer install
python -m app.services.db.assistant_schema_installer verify
```

`verify` is read-only and reports `ABSENT`, `EXACT`, `PARTIAL`, or `MISMATCH`. The exact historical Core V1 catalog is reported as a sanitized, explicitly upgradeable `PARTIAL`; every other partial or mismatched shape is a hard stop. `install` takes a PostgreSQL transactional advisory lock, creates the full canonical schema from `ABSENT`, transactionally upgrades only the exact historical Core V1 shape, verifies before commit, and is a no-op from `EXACT`. It never repairs arbitrary `PARTIAL` or `MISMATCH` states. The installer is not imported by API startup, worker startup, `init_db()`, or lightweight migrations. No Alembic stamp is used. Keep `SITEFORMO_ASSISTANT_ENABLED=false` throughout schema installation and verification.

## API and transport

- `POST /api/journey/session` creates or resumes the neutral funnel visitor without creating Assistant state.
- `GET /api/assistant/availability` returns only the feature-enabled boolean and performs no Assistant DB or OpenAI work.
- `POST /api/assistant/session` creates or resumes a Journey-owned Assistant conversation and returns bounded history.
- `GET /api/assistant/history` requires the Journey credential header and verifies conversation ownership.
- `POST /api/assistant/message` requires `client_message_id` and streams `start`, `delta`, `done`, or sanitized `error` SSE events.

State-changing Assistant requests require the exact Origin `https://ie.siteformo.com`. A conversation ID alone grants no access. User turns are committed once before inference; an Assistant turn is committed only after a complete successful stream. Recent model context is bounded to 40 completed messages.

The Journey V1 browser contract uses the existing direct Railway convention. On initial `ie.siteformo.com` page load, frontend code calls `POST /api/journey/session`, stores the returned scoped credential, and reuses it for later Examples, Forms, and Assistant requests. Examples and Forms are not yet bound in this version. Assistant calls use the same credential header and retain exact `Origin: https://ie.siteformo.com` enforcement. `/message` remains real streamed SSE consumed from the response body. WordPress/WPCode integration remains a separately reviewed downstream change.

The existing homepage SiteFormo Support modal remains the sole UI owner and is not changed by Journey V1: it stays open during scrolling and closes only through its existing close control. Scroll-collapse is reserved for a later, separately reviewed downstream-funnel launcher integration.

## Deployment warning

The observed Railway Watch Path is `backend/src/**`, while runtime and migration changes live under `backend/app/**`, `backend/alembic/**`, and tests under `backend/tests/**`. This must be corrected before relying on automatic deployment. Railway configuration is intentionally unchanged here.

## Known limits

The rate limiter is process-local and only a first layer. There is no production widget, distributed limiter, retention/deletion workflow, moderation policy, persona, journey policy, customer/project binding, or action capability in V1. PostgreSQL migration and proxy/cookie behavior must be verified in isolated/staging environments before production use.
