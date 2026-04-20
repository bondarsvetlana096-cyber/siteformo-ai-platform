Applied fixes in this package:

1. Mandatory demo overlay on every generated landing:
- top notice showing free demo limit
- fixed CTA button leading to the main Siteformo website
- CTA click tracking via /api/requests/{request_id}/events

2. Generation pipeline improved:
- keeps the same business type and niche
- keeps the same source language when possible
- avoids turning redesigns into generic agency pages
- reuses source image URLs when available
- better source-guided fallback HTML if model output is missing

3. Scraper improved:
- collects source images
- extracts meta description
- detects source language

4. Storage/runtime hardening:
- STORAGE_BACKEND defaults to auto
- auto selects Supabase when Supabase credentials are present
- local fallback still works for local development

5. Limit bypass hardening:
- BYPASS_LIMIT_EMAILS supported via settings/env
- klon97048@gmail.com included by default

6. Env examples updated:
- BYPASS_LIMIT_EMAILS added
- local example uses STORAGE_BACKEND=auto


## ChatGPT patch update
- strengthened niche lock in `backend/app/services/generation.py`
- added forbidden generic SaaS/agency/B2B wording penalties in scoring
- increased preference for real source images in generated HTML
- added AI logging for source analysis, final prompt, and scoring results
- enforced mobile-first responsive requirements in generation prompts
- hardened overlay CSS for phone screens and prevented overlay from covering content
- kept CTA destination fixed to `https://siteformo.com`
